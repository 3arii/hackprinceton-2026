"use client";

type Props = {
  selected: boolean;
  buoyCount: number;
  onSelect(): void;
  onHover(hovering: boolean): void;
};

export default function NetworkPanel({ selected, buoyCount, onSelect, onHover }: Props) {
  return (
    <nav className="mesh-panel" aria-label="Mesh networks">
      <h2 className="mesh-panel-title">Mesh Networks</h2>
      <ul>
        <li>
          <button
            type="button"
            className="mesh-item"
            aria-pressed={selected}
            onClick={onSelect}
            onPointerEnter={() => onHover(true)}
            onPointerLeave={() => onHover(false)}
            onFocus={() => onHover(true)}
            onBlur={() => onHover(false)}
          >
            <svg viewBox="0 0 16 16" aria-hidden="true" className="mesh-icon">
              <path d="M8 3.5 3.5 12h9z" />
              <circle cx="8" cy="3.5" r="1.6" />
              <circle cx="3.5" cy="12" r="1.6" />
              <circle cx="12.5" cy="12" r="1.6" />
            </svg>
            <span className="mesh-item-text">
              <span className="mesh-item-name">Mesh Network 1</span>
              <span className="mesh-item-meta">{buoyCount} buoys · Lake Carnegie</span>
            </span>
          </button>
        </li>
      </ul>
    </nav>
  );
}
