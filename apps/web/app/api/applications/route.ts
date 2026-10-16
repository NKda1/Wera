import { handle } from "@/lib/server/http";
import { listApplications } from "@/lib/server/store";

export async function GET() {
  return handle(() => ({ items: listApplications() }));
}
