import type { Metadata } from "next";
import PhotoFeed from "@/components/PhotoFeed";

export const metadata: Metadata = { title: "Buoy camera" };

export default function ImagesPage() {
  return <PhotoFeed />;
}
