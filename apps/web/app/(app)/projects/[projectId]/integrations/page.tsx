import Link from "next/link";
import { notFound } from "next/navigation";

import { IntegrationCard } from "@/components/integrations/integration-card";
import { getProject, getProjectIntegrations } from "@/lib/api/server";

export const metadata = { title: "Integrations · Product Intelligence" };

export default async function IntegrationsPage({
  params,
}: {
  params: Promise<{ projectId: string }>;
}) {
  const { projectId } = await params;
  const [project, integrations] = await Promise.all([
    getProject(projectId),
    getProjectIntegrations(projectId),
  ]);
  if (!project || !integrations) notFound();

  return (
    <div className="space-y-6">
      <div className="space-y-1">
        <Link
          href={`/projects/${projectId}`}
          className="text-sm text-muted-foreground hover:underline"
        >
          ← {project.name}
        </Link>
        <h1 className="text-2xl font-semibold tracking-tight">Integrations</h1>
        <p className="max-w-2xl text-sm text-muted-foreground">
          Connect the tools this project already uses. Once connected, they
          provide the data behind everything the platform reports on
          {" "}
          {project.website_url}.
        </p>
      </div>

      <ul className="space-y-4">
        {integrations.map((entry) => (
          <li key={entry.provider}>
            <IntegrationCard entry={entry} />
          </li>
        ))}
      </ul>
    </div>
  );
}
