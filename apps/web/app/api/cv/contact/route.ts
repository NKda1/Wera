import { forward } from "@/lib/server/agent";

/** Contact details are stored apart from the CV and never sent to a model (CV-06). */
export async function GET(req: Request) {
  return forward(req, "/cv/contact");
}

export async function PUT(req: Request) {
  return forward(req, "/cv/contact");
}
