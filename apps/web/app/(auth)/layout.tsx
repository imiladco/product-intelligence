import { redirect } from "next/navigation";

import { getSession } from "@/lib/api/server";

export default async function AuthLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  // Someone already signed in has no business on the sign-in screens.
  const session = await getSession();
  if (session) redirect("/projects");

  return (
    <main className="flex min-h-svh flex-col items-center justify-center gap-6 bg-muted/30 p-6">
      <div className="w-full max-w-sm">{children}</div>
    </main>
  );
}
