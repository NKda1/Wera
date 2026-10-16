import { forward } from "@/lib/server/agent";

export async function GET(req: Request) {
  return forward(req, "/profile");
}

export async function PUT(req: Request) {
  return forward(req, "/profile");
}
