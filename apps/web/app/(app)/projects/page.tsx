import Link from "next/link";

import { NewProjectDialog } from "@/components/projects/new-project-dialog";
import { Card, CardContent } from "@/components/ui/card";
import { getProjects, getSession } from "@/lib/api/server";

export const metadata = { title: "Projects · Product Intelligence" };

export default async function ProjectsPage() {
  const [projects, session] = await Promise.all([getProjects(), getSession()]);
  const workspaces = session?.workspaces ?? [];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Projects</h1>
          <p className="text-sm text-muted-foreground">
            A project is one digital product you want to connect data sources to.
          </p>
        </div>
        <NewProjectDialog workspaces={workspaces} />
      </div>

      {projects.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center gap-3 py-14 text-center">
            <p className="font-medium">No projects yet</p>
            <p className="max-w-sm text-sm text-muted-foreground">
              Create your first project and enter the website it represents.
            </p>
            <NewProjectDialog workspaces={workspaces} />
          </CardContent>
        </Card>
      ) : (
        <ul className="grid gap-4 sm:grid-cols-2">
          {projects.map((project) => (
            <li key={project.id}>
              <Link href={`/projects/${project.id}`} className="block">
                <Card className="transition-colors hover:border-foreground/20">
                  <CardContent className="space-y-1">
                    <p className="font-medium">{project.name}</p>
                    <p className="truncate text-sm text-muted-foreground">
                      {project.website_url}
                    </p>
                  </CardContent>
                </Card>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
