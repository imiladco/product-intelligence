import Link from "next/link";

import { AuthForm } from "@/components/auth/auth-form";

export const metadata = { title: "Sign in · Product Intelligence" };

export default function LoginPage() {
  return (
    <AuthForm
      mode="login"
      title="Sign in"
      description="Access your projects and integrations."
      submitLabel="Sign in"
      footer={
        <>
          Don&apos;t have an account?{" "}
          <Link href="/signup" className="font-medium text-foreground underline-offset-4 hover:underline">
            Create one
          </Link>
        </>
      }
    />
  );
}
