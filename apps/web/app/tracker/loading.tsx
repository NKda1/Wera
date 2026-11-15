import { ScreenSkeleton } from "@/lib/ui/Skeleton";

export default function Loading() {
  return <ScreenSkeleton label="Loading the tracker" rows={3} />;
}
