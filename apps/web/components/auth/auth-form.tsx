"use client";

import { useRouter } from "next/navigation";
import { type ReactNode, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { apiFetch } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";
import type { Session } from "@/lib/api/types";

interface AuthFormProps {
  mode: "login" | "signup";
  title: string;
  description: string;
  submitLabel: string;
  footer: ReactNode;
}

export function AuthForm({ mode, title, description, submitLabel, footer }: AuthFormProps) {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [pending, setPending] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    setFormError(null);
    setFieldErrors({});

    try {
      const body =
        mode === "signup" ? { email, password, name } : { email, password };
      await apiFetch<Session>(`/auth/${mode}`, { method: "POST", body });
      // Server components read the session cookie, so refresh before navigating.
      router.replace("/projects");
      router.refresh();
    } catch (error) {
      if (error instanceof ApiError) {
        const fields: Record<string, string> = {};
        for (const [field, messages] of Object.entries(error.fieldErrors)) {
          if (field !== "non_field_errors" && messages[0]) fields[field] = messages[0];
        }
        setFieldErrors(fields);
        setFormError(Object.keys(fields).length ? null : error.message);
      } else {
        setFormError("Could not reach the server. Please try again.");
      }
      setPending(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <form onSubmit={onSubmit} noValidate>
        <CardContent className="space-y-4">
          {formError ? (
            <p role="alert" className="text-sm text-destructive">
              {formError}
            </p>
          ) : null}

          {mode === "signup" ? (
            <div className="space-y-2">
              <Label htmlFor="name">Name</Label>
              <Input
                id="name"
                autoComplete="name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                aria-invalid={Boolean(fieldErrors.name)}
              />
              <FieldError message={fieldErrors.name} />
            </div>
          ) : null}

          <div className="space-y-2">
            <Label htmlFor="email">Email</Label>
            <Input
              id="email"
              type="email"
              required
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              aria-invalid={Boolean(fieldErrors.email)}
            />
            <FieldError message={fieldErrors.email} />
          </div>

          <div className="space-y-2">
            <Label htmlFor="password">Password</Label>
            <Input
              id="password"
              type="password"
              required
              autoComplete={mode === "signup" ? "new-password" : "current-password"}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              aria-invalid={Boolean(fieldErrors.password)}
            />
            <FieldError message={fieldErrors.password} />
            {mode === "signup" && !fieldErrors.password ? (
              <p className="text-xs text-muted-foreground">At least 10 characters.</p>
            ) : null}
          </div>
        </CardContent>

        <CardFooter className="mt-6 flex-col items-stretch gap-4">
          <Button type="submit" disabled={pending}>
            {pending ? "Please wait…" : submitLabel}
          </Button>
          <p className="text-center text-sm text-muted-foreground">{footer}</p>
        </CardFooter>
      </form>
    </Card>
  );
}

function FieldError({ message }: { message?: string }) {
  if (!message) return null;
  return <p className="text-sm text-destructive">{message}</p>;
}
