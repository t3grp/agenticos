"use client";

import { useEffect, useState } from "react";
import { Download, File } from "lucide-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Button, Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui";
import { getErrorMessage } from "@/lib/api-error";
import { formatBytes } from "@/lib/utils";
import { downloadWorkflowRunFile, getWorkflowRunFileBlob } from "@/lib/workflows/runs-api";
import type { WorkflowRunFile } from "@/lib/workflows/types";

/**
 * The files a run's steps stored - downloads, rendered pages, a script's output -
 * each with a way to save it.
 */
export function RunFiles({ runId, files }: { runId: string; files: WorkflowRunFile[] }) {
  const t = useTranslations("pages.workflows");
  const tErrors = useTranslations("errors");
  const [saving, setSaving] = useState<string | null>(null);

  async function save(file: WorkflowRunFile) {
    setSaving(file.id);
    try {
      await downloadWorkflowRunFile(runId, file);
    } catch (error) {
      toast.error(getErrorMessage(error, tErrors));
    } finally {
      setSaving(null);
    }
  }

  if (files.length === 0)
    return (
      <p className="text-muted-foreground rounded-lg border px-4 py-3 text-xs">
        {t("runFilesNone")}
      </p>
    );

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm">{t("runFiles")}</CardTitle>
        <CardDescription className="text-xs">{t("runFilesCaption")}</CardDescription>
      </CardHeader>
      <CardContent>
        <ul className="divide-border divide-y">
          {files.map((file) => {
            const name = file.filename ?? file.id;
            return (
              <li key={file.id} className="space-y-2 py-2">
                <div className="flex items-center gap-2">
                  <File aria-hidden="true" className="text-muted-foreground size-4 shrink-0" />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm">{name}</p>
                    <p className="text-muted-foreground text-xs">
                      {file.content_type} · {formatBytes(file.byte_size)}
                    </p>
                  </div>
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label={t("runFileDownload", { name })}
                    disabled={saving === file.id}
                    onClick={() => void save(file)}
                  >
                    <Download className="h-4 w-4" />
                  </Button>
                </div>
                <RunFilePreview runId={runId} file={file} />
              </li>
            );
          })}
        </ul>
      </CardContent>
    </Card>
  );
}

function RunFilePreview({ runId, file }: { runId: string; file: WorkflowRunFile }) {
  const t = useTranslations("pages.workflows");
  const [src, setSrc] = useState<string | null>(null);
  const contentType = file.content_type.split(";", 1)[0]?.toLowerCase();
  const previewable =
    contentType?.startsWith("image/") === true ||
    contentType === "video/mp4" ||
    contentType === "video/webm";

  useEffect(() => {
    if (!previewable) return;
    let disposed = false;
    let objectUrl: string | null = null;
    void getWorkflowRunFileBlob(runId, file.id)
      .then((blob) => {
        if (disposed) return;
        objectUrl = URL.createObjectURL(blob);
        setSrc(objectUrl);
      })
      .catch(() => setSrc(null));
    return () => {
      disposed = true;
      if (objectUrl !== null) URL.revokeObjectURL(objectUrl);
    };
  }, [file.id, previewable, runId]);

  if (!previewable || src === null) return null;
  const label = t("runFilePreview", { name: file.filename ?? file.id });
  if (contentType?.startsWith("image/") === true) {
    return (
      <img
        src={src}
        alt={label}
        className="max-h-72 max-w-full rounded-md object-contain"
        loading="lazy"
      />
    );
  }
  return (
    <video
      src={src}
      controls
      preload="metadata"
      aria-label={label}
      className="max-h-72 max-w-full rounded-md"
    />
  );
}
