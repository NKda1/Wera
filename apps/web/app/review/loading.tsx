import { ScreenSkeleton } from "@/lib/ui/Skeleton";

export default function Loading() {
  return <ScreenSkeleton label="Loading your packs" rows={3} />;
}
