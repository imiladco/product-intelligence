import { redirect } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import { getSession } from "@/lib/api/server";

export default async function AppLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  // The authoritative check is on the API for every request; this guard exists
  // so signed-out visitors get the sign-in page instead of an error screen.
  const session = await getSession();
  if (!session) redirect("/login");

  return <AppShell session={session}>{children}</AppShell>;
}
