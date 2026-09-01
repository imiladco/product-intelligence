import Link from "next/link";

import { SignOutButton } from "@/components/auth/sign-out-button";
import type { Session } from "@/lib/api/types";

export function AppShell({
  session,
  children,
}: {
  session: Session;
  children: React.ReactNode;
}) {
  const workspace = session.workspaces[0];

  return (
    <div className="min-h-svh bg-muted/30">
      <header className="border-b bg-background">
        <div className="mx-auto flex h-14 max-w-5xl items-center justify-between gap-4 px-6">
          <div className="flex items-baseline gap-3">
            <Link href="/projects" className="text-sm font-semibold">
              Product Intelligence
            </Link>
            {workspace ? (
              <span className="text-sm text-muted-foreground">{workspace.name}</span>
            ) : null}
          </div>
          <div className="flex items-center gap-3">
            <span className="hidden text-sm text-muted-foreground sm:inline">
              {session.user.email}
            </span>
            <SignOutButton />
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-5xl px-6 py-10">{children}</main>
    </div>
  );
}
