import Link from "next/link";

import { AuthForm } from "@/components/auth/auth-form";

export const metadata = { title: "Create account · Product Intelligence" };

export default function SignupPage() {
  return (
    <AuthForm
      mode="signup"
      title="Create your account"
      description="A workspace is created for you automatically."
      submitLabel="Create account"
      footer={
        <>
          Already have an account?{" "}
          <Link href="/login" className="font-medium text-foreground underline-offset-4 hover:underline">
            Sign in
          </Link>
        </>
      }
    />
  );
}
