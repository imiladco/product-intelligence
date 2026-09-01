import { redirect } from "next/navigation";

import { getSession } from "@/lib/api/server";

export default async function Home() {
  const session = await getSession();
  redirect(session ? "/projects" : "/login");
}
