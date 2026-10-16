import { handle, readJson } from "@/lib/server/http";
import { ContactSchema, parse } from "@/lib/server/schemas";
import { getContact, putContact } from "@/lib/server/store";

export async function GET() {
  return handle(() => getContact());
}

export async function PUT(req: Request) {
  return handle(async () => putContact(parse(ContactSchema, await readJson(req))));
}
