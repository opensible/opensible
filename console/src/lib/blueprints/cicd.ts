import type { BlueprintGroup } from "./types";

const LOGO = (slug: string, color?: string) =>
  `https://cdn.simpleicons.org/${slug}${color ? `/${color}` : ""}`;

export const cicdGroup: BlueprintGroup = {
  id: "cicd",
  name: "CI/CD",
  description: "Build, push and deploy container images from Git.",
  logo: LOGO("githubactions", "2088FF"),
  blueprints: [
    {
      id: "cicd-git-build-deploy",
      name: "CI/CD Pipeline (Git → Build → Push → Deploy)",
      description:
        "Clone a Git repo (GitHub / GitLab / Gitea / Forgejo), build a container image with docker buildx, push to any OCI registry (Harbor, Sonatype Nexus, Zot, Docker Hub, GHCR, GitLab), and optionally deploy to a target host via docker or docker compose.",
      logo: LOGO("githubactions", "2088FF"),
      tags: ["cicd", "docker", "git", "registry", "deploy"],
      author: "opensible",
      stars: 12,
      available: true,
      path: "IaC/blueprints/cicd/git-build-deploy",
      templateId: "cicd-pipeline",
      filenameStem: "cicd-pipeline",
      defaults: {
        git_repo_url: "",
        git_ref: "main",
        dockerfile_path: "Dockerfile",
        build_context: ".",
        platforms: "linux/amd64",
        registry_url: "",
        registry_type: "harbor",
        registry_username: "",
        registry_password: "",
        registry_insecure: false,
        image_repository: "",
        image_tag: "latest",
        also_tag_git_sha: true,
        deploy_enabled: false,
        deploy_target_host: "",
        deploy_mode: "docker-run",
        container_name: "app",
        container_ports: "",
        container_env: "",
        compose_file_path: "/opt/app/docker-compose.yml",
        workdir: "/var/lib/opensible/cicd",
        become: true,
      },
    },
  ],
};
