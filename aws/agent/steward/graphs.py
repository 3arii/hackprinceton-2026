"""The deployment's graphs, registered together in langgraph.json.

    steward     supervisor deep agent: plans, launches the specialists as async subagents,
                weighs their evidence, decides, alerts, and keeps long-term memory
    hydrology   river gauge + weather specialist
    satellite   Sentinel-2 imagery specialist
    permits     EPA discharge-permit specialist
    community   Bluesky community-signals specialist (content + connectivity)
    history     buoy-network and past-incident specialist

All five are deep agents on the same Agent Server, so the specialists run on their own
threads concurrently and wake the steward when they finish (ASGI transport, no URLs).
"""

from deepagents import AsyncSubAgent, create_deep_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, TodoListMiddleware

from steward import tools
from steward.config import supervisor_model, worker_model
from steward.harness import READ_ONLY_MEMORY, READ_ONLY_SKILLS, Case, Chat, backend

EVIDENCE_RULES = """
## Report
1. Write your full findings to /case/evidence/{name}.md: a short headline, the numbers that matter (with dates and units), and what they mean for the hypotheses you were asked about. This file is read by the lead investigator and shown to the lake manager, so make it self-contained.
2. Then reply with a summary of at most 120 words: your conclusion first, then the two or three numbers that support it, then anything you could not check.
Report only what the tools returned. Never invent readings. If a source fails, say so and continue with the others.
"""


def specialist(name: str, role: str, tool_list: list):
    return create_deep_agent(
        model=worker_model(),
        tools=tool_list,
        system_prompt=role + EVIDENCE_RULES.format(name=name),
        backend=backend(),
        permissions=[READ_ONLY_SKILLS, READ_ONLY_MEMORY],
        context_schema=Case,
        middleware=[ModelCallLimitMiddleware(run_limit=10, exit_behavior="end")],
        name=name,
    )


hydrology = specialist(
    "hydrology",
    """You are the hydrology specialist on a water-quality investigation at Lake Carnegie, Princeton NJ.
You test whether weather and the river explain a buoy anomaly. Use river_gauge and weather (call them together), and buoy_network when you need the lake-wide picture.
Look for: heatwaves (air highs well above normal, river warming over days), storm runoff (heavy rain, rising discharge and turbidity), road salt (snow then melt, rising conductance), and whether the river moved the same way as the lake.""",
    [tools.river_gauge, tools.weather, tools.buoy_network],
)

satellite = specialist(
    "satellite",
    """You are the remote-sensing specialist on a water-quality investigation at Lake Carnegie, Princeton NJ.
Use analyze_lake_imagery to measure the lake in recent Sentinel-2 scenes from the AWS Open Data registry. Then read /case/satellite/lake.png with read_file and describe what you see: water colour, any plume, sediment or algae, especially near the buoys (red squares) and the inflows.
Compare the newest clear scene's turbidity index (NDTI) and chlorophyll index (NDCI) with the median of the others. Say how many days old the newest clear scene is: Sentinel-2 passes every few days and clouds hide the lake, so imagery may predate the anomaly. Note that Sentinel-2 has no thermal band and cannot see water temperature.""",
    [tools.analyze_lake_imagery, tools.satellite_scenes],
)

permits = specialist(
    "permits",
    """You are the regulatory specialist on a water-quality investigation at Lake Carnegie, Princeton NJ.
Use permitted_dischargers to list facilities with Clean Water Act discharge permits, then facility_compliance on the ones most relevant to the anomaly: the closest to the anomalous buoy, and any whose kind of discharge fits the anomaly (sewage plants for conductivity and microplastics, industrial cooling for heat). Check at most five facilities.
Rank plausible sources by distance, discharge type and compliance record (quarters in noncompliance, significant-noncomplier status). If nothing is plausible, say so plainly.""",
    [tools.permitted_dischargers, tools.facility_compliance],
)

history = specialist(
    "history",
    """You are the data-history specialist on a water-quality investigation at Lake Carnegie, Princeton NJ.
Use buoy_network to compare all four buoys around the anomaly: is it one buoy or the whole lake, did it rise gradually or jump in one reading, and did other metrics on the same buoy move with it (a jump in one metric alone suggests a sensor fault)?
Use past_incidents to find earlier cases on the same buoy or metric and how they were resolved.""",
    [tools.buoy_network, tools.past_incidents],
)

community = specialist(
    "community",
    """You are the community-signals specialist on a water-quality investigation at Lake Carnegie, Princeton NJ.
You check what people near the lake are saying on Bluesky around the time of the anomaly, and whether that chatter is genuine.
1. Run 4 to 6 search_bluesky queries for what people would write if the hypotheses were true: symptoms (heat, smell, muddy or discoloured water, dead fish, foam, spill, sirens) combined with places (Princeton, Lake Carnegie, Carnegie Lake, Millstone River, Stony Brook, Mercer County, Trenton, New Jersey). Start local and specific, then widen.
2. Run analyze_conversation once on everything collected.
3. Judge: do independent local accounts corroborate a natural cause (a heatwave, a storm) or report pollution signs (smell, dead fish, discharge)? Is any of it coordinated, automated or off-topic? Name the clusters by what they are (news outlets, transit alerts, residents, campaigns).
Quote at most three short posts as evidence, with handle and time. Absence of chatter is weak evidence: few people post about a small lake.""",
    [tools.search_bluesky, tools.analyze_conversation],
)


SPECIALISTS: list[AsyncSubAgent] = [
    AsyncSubAgent(
        name="hydrology",
        graph_id="hydrology",
        description="River gauge and weather specialist. Tests heatwave, storm-runoff and road-salt explanations using the USGS Millstone River gauge, Stony Brook inflow, Open-Meteo history with 10-year normals, and NWS alerts.",
    ),
    AsyncSubAgent(
        name="satellite",
        graph_id="satellite",
        description="Sentinel-2 imagery specialist (AWS Open Data). Measures turbidity and chlorophyll over the lake's open water in recent scenes, looks at a true-color image for plumes, sediment or algae near the buoys. Cannot see temperature.",
    ),
    AsyncSubAgent(
        name="permits",
        graph_id="permits",
        description="Regulatory specialist. Finds EPA-permitted dischargers near the lake and checks their location relative to each buoy, discharge type and Clean Water Act compliance record, to rank plausible pollution sources.",
    ),
    AsyncSubAgent(
        name="community",
        graph_id="community",
        description="Community-signals specialist (Bluesky). Searches public posts near the lake around the anomaly time for corroborating reports (heat, smell, discoloured water, dead fish, spills), maps who is connected to whom, and separates independent local voices from coordinated or automated accounts.",
    ),
    AsyncSubAgent(
        name="history",
        graph_id="history",
        description="Buoy-network and history specialist. Compares all four buoys (local vs lake-wide, gradual vs sudden, sensor-fault signs) and finds past incidents on the same buoy or metric.",
    ),
]

STEWARD_PROMPT = """You are the Lake Steward, the lead investigator for a network of four water-quality buoys on Lake Carnegie in Princeton, New Jersey. When a buoy reports an extreme reading, you decide whether it has a natural explanation or needs the lake manager's attention now.

You lead a team of specialists who run in the background as async subagents: hydrology, satellite, permits, community and history. They work in parallel on their own threads, write their findings to /case/evidence/, and you are notified as each one finishes.

## When a new anomaly arrives
1. Read the playbook skill for the anomaly's metric.
2. Write a short plan with write_todos: the competing hypotheses and which specialist tests each.
3. Launch every specialist the playbook calls for in one turn with start_async_task, each with a specific, self-contained task: the buoy, metric, value, time, and exactly which hypotheses to test. They see only the task you give them.
4. Then stop and wait. Do not call check_async_task right after launching; you will be woken as each specialist finishes.

## As specialists report back
- Call write_todos to mark that specialist's todo completed. While others are still running, say in one line what you learned and wait again.
- If a finding raises a sharper question, steer: use update_async_task to give a running specialist a follow-up, or start a new task. Do this at most twice per case.
- Once every specialist has reported (check with list_async_tasks), read every file in /case/evidence/ with read_file before deciding: notifications carry only short summaries, the files hold the numbers.

## Deciding
- benign: a natural cause clearly explains the reading.
- monitor: unexplained, but no sign of harm yet.
- warn: likely pollution, or conditions dangerous to wildlife or people whatever the cause.
Weigh the evidence against each hypothesis. Call submit_verdict once (it also texts the verdict to the lake manager), then mark the last todo completed. If you warn, load the manager-alert skill and call send_alert with what the manager should do now.

## Memory
/memories/AGENTS.md holds what you have learned about this lake. After deciding, add one durable lesson there with edit_file only if this case taught you something that will change a future decision (a buoy's quirk, a seasonal pattern, a facility to watch). Do not log routine cases.

Be concise. Cite real numbers. Never invent data."""

steward = create_deep_agent(
    model=supervisor_model(),
    tools=[tools.buoy_network, tools.submit_verdict, tools.send_alert],
    system_prompt=STEWARD_PROMPT,
    subagents=SPECIALISTS,
    skills=["/skills/"],
    memory=["/memories/AGENTS.md"],
    backend=backend(),
    permissions=[READ_ONLY_SKILLS],
    context_schema=Case,
    middleware=[TodoListMiddleware(), ModelCallLimitMiddleware(run_limit=14, exit_behavior="end")],
    name="steward",
)


# --- Liaison: answers the lake manager's iMessages ---------------------------------------

LIAISON_PROMPT = """You are the Lake Steward's voice on iMessage. The person texting you manages Lake Carnegie in Princeton, NJ; you alert them when the steward's investigations find a problem, and they text you to ask about it.
- For "why", "what happened", "details" or similar: use recent_cases, then case_evidence for the specialists that matter, and explain the evidence behind the latest verdict in plain words with the key numbers.
- For "how's the lake" or "status": use lake_now.
- For anything else, answer briefly from what you know about the lake (your memory below) or say what you can do.
Always finish by calling reply exactly once with your answer: under 100 words, plain text, no markdown, friendly and direct. Never invent readings."""


liaison = create_deep_agent(
    model=worker_model(),
    tools=[tools.recent_cases, tools.case_evidence, tools.lake_now, tools.reply],
    system_prompt=LIAISON_PROMPT,
    memory=["/memories/AGENTS.md"],
    backend=backend(),
    permissions=[READ_ONLY_SKILLS, READ_ONLY_MEMORY],
    context_schema=Chat,
    middleware=[ModelCallLimitMiddleware(run_limit=6, exit_behavior="end")],
    name="liaison",
)
