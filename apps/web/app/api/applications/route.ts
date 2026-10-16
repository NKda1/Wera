import { forward } from "@/lib/server/agent";

export async function GET(req: Request) {
  return forward(req, "/applications");
}
