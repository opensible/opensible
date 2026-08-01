import { createFileRoute, Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import {
  createColumnHelper, flexRender, getCoreRowModel, getFilteredRowModel,
  getPaginationRowModel, getSortedRowModel, useReactTable, type SortingState,
} from "@tanstack/react-table";
import { useMemo, useState } from "react";
import { Plus, Cloud, ArrowUpDown, ChevronLeft, ChevronRight } from "lucide-react";
import { Breadcrumbs } from "@/components/app-shell/Breadcrumbs";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge, statusToVariant } from "@/components/ui/badge";
import { api } from "@/lib/api";
import { qk } from "@/lib/query";
import { useT } from "@/lib/i18n";
import { ProviderCell } from "@/lib/providers";

export const Route = createFileRoute("/cloud/stacks/")({ component: StacksList });

type StackRow = {
  name: string;
  provider?: string;
  env?: string;
  cloud_project?: string;
  region?: string;
  last_action?: string;
  last_status?: string;
  drift_status?: string;
  updated_at?: number;
};

const col = createColumnHelper<StackRow>();

function StacksList() {
  const t = useT();
  const { data, isLoading: loading, error: queryError } = useQuery({
    queryKey: qk.stacks,
    queryFn: () => api<{ stacks: StackRow[] }>("GET", "/api/cloud/stacks"),
  });
  const rows: StackRow[] = data?.stacks ?? [];
  const error = queryError ? ((queryError as Error).message || "Failed to load") : null;

  const [sorting, setSorting] = useState<SortingState>([]);
  const [globalFilter, setGlobalFilter] = useState("");

  const columns = useMemo(() => [
    col.accessor("provider", { header: "Provider", cell: (c) => <ProviderCell id={c.getValue()} /> }),
    col.accessor("name", {
      header: "Name",
      cell: (c) => (
        <Link to="/cloud/stacks/$stackId" params={{ stackId: c.getValue() }}
          className="text-[var(--color-primary)] hover:underline font-medium">
          {c.getValue()}
        </Link>
      ),
    }),
    col.accessor("cloud_project", {
      header: "Cloud Project",
      cell: (c) => c.getValue() || <span className="text-[var(--color-muted-foreground)]">—</span>,
    }),
    col.accessor("env", { header: "Env", cell: (c) => c.getValue() || "—" }),
    col.accessor("region", {
      header: "Region",
      cell: (c) => c.getValue() || <span className="text-[var(--color-muted-foreground)]">—</span>,
    }),
    col.accessor("last_action", { header: "Last Action", cell: (c) => c.getValue() || "—" }),
    col.accessor("last_status", {
      header: "Status",
      cell: (c) => {
        const v = c.getValue();
        return v ? <Badge variant={statusToVariant(v)}>{v}</Badge> : "—";
      },
    }),
    col.accessor("drift_status", {
      header: "Drift",
      cell: (c) => {
        const v = c.getValue();
        if (!v || v === "disabled") return <span className="text-[var(--color-muted-foreground)]">Off</span>;
        if (v === "in_sync") return <Badge variant="success">In sync</Badge>;
        if (v === "drifted") return <Badge variant="warning">Drift</Badge>;
        if (v === "checking") return <Badge variant="primary">Checking</Badge>;
        if (v === "error") return <Badge variant="destructive">Failed</Badge>;
        return <Badge variant="default">Not checked</Badge>;
      },
    }),
    col.accessor("updated_at", {
      header: "Updated",
      cell: (c) => {
        const v = c.getValue();
        return <span className="text-[var(--color-muted-foreground)]">{v ? new Date(v * 1000).toLocaleString() : "—"}</span>;
      },
    }),
    col.display({
      id: "actions",
      header: "",
      cell: (c) => (
        <Button variant="outline" size="sm" asChild>
          <Link to="/cloud/stacks/$stackId" params={{ stackId: c.row.original.name }}>Open</Link>
        </Button>
      ),
    }),
  ], []);

  const table = useReactTable({
    data: rows,
    columns,
    state: { sorting, globalFilter },
    onSortingChange: setSorting,
    onGlobalFilterChange: setGlobalFilter,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    initialState: { pagination: { pageSize: 20 } },
  });

  return (
    <div className="space-y-6">
      <Breadcrumbs items={[{ label: "Cloud Stacks" }]} />
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold">{t("cloud.stacks.title")}</h1>
          <p className="text-sm text-[var(--color-muted-foreground)] mt-1">
            OpenTofu/Terraform stacks managed by this control plane.
          </p>
        </div>
        <Button asChild>
          <Link to="/cloud/stacks/new"><Plus className="h-4 w-4" /> {t("cloud.stacks.new")}</Link>
        </Button>
      </div>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-4">
          <CardTitle className="text-base">
            {loading ? "Loading…" : `${table.getFilteredRowModel().rows.length} of ${rows.length} stack${rows.length === 1 ? "" : "s"}`}
          </CardTitle>
          <Input
            placeholder="Search…"
            value={globalFilter}
            onChange={(e) => setGlobalFilter(e.target.value)}
            className="max-w-xs"
          />
        </CardHeader>
        <CardContent className="p-0 overflow-x-auto">
          {error && <div className="px-6 py-8 text-center text-[var(--color-destructive)] text-sm">{error}</div>}
          {!error && rows.length === 0 && !loading && (
            <div className="px-6 py-12 text-center">
              <Cloud className="h-10 w-10 mx-auto text-[var(--color-muted-foreground)] opacity-40 mb-3" />
              <div className="text-sm font-medium mb-1">No cloud stacks yet</div>
              <p className="text-sm text-[var(--color-muted-foreground)] mb-4">Create one to start provisioning infrastructure.</p>
              <Button asChild size="sm"><Link to="/cloud/stacks/new">Create Stack</Link></Button>
            </div>
          )}
          {!error && rows.length > 0 && (
            <>
              <table className="w-full text-sm min-w-[1100px]">
                <thead className="text-xs uppercase tracking-wider text-[var(--color-muted-foreground)] border-b border-[var(--color-border)]">
                  {table.getHeaderGroups().map(hg => (
                    <tr key={hg.id}>
                      {hg.headers.map(h => (
                        <th key={h.id} className="text-left px-4 py-3 font-medium whitespace-nowrap">
                          {h.isPlaceholder ? null : h.column.getCanSort() ? (
                            <button
                              type="button"
                              onClick={h.column.getToggleSortingHandler()}
                              className="inline-flex items-center gap-1 hover:text-[var(--color-foreground)] whitespace-nowrap"
                            >
                              {flexRender(h.column.columnDef.header, h.getContext())}
                              <ArrowUpDown className="h-3 w-3 opacity-50 shrink-0" />
                            </button>
                          ) : (
                            flexRender(h.column.columnDef.header, h.getContext())
                          )}
                        </th>
                      ))}
                    </tr>
                  ))}
                </thead>
                <tbody className="divide-y divide-[var(--color-border)]/40">
                  {table.getRowModel().rows.map(row => (
                    <tr key={row.id} className="hover:bg-[var(--color-muted)]/50">
                      {row.getVisibleCells().map(cell => (
                        <td key={cell.id} className="px-4 py-3 whitespace-nowrap">
                          {flexRender(cell.column.columnDef.cell, cell.getContext())}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
              {table.getPageCount() > 1 && (
                <div className="flex items-center justify-end gap-2 px-6 py-3 border-t border-[var(--color-border)] text-xs">
                  <span className="text-[var(--color-muted-foreground)]">
                    Page {table.getState().pagination.pageIndex + 1} of {table.getPageCount()}
                  </span>
                  <Button variant="outline" size="sm" onClick={() => table.previousPage()} disabled={!table.getCanPreviousPage()}>
                    <ChevronLeft className="h-4 w-4" />
                  </Button>
                  <Button variant="outline" size="sm" onClick={() => table.nextPage()} disabled={!table.getCanNextPage()}>
                    <ChevronRight className="h-4 w-4" />
                  </Button>
                </div>
              )}
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
