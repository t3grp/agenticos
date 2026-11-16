"use client";

import { useTranslations } from "next-intl";

import {
  Input,
  Label,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui";
import { useImageProviders, useVideoProviders } from "@/hooks/use-model-providers";
import type { VideoModel } from "@/hooks/use-model-providers";
import type { NodeInstance, Uuid } from "@/lib/workflows/types";
import type { Schema } from "./schema-model";

interface MediaModelFieldProps {
  definitionId: "image.generate" | "video.generate";
  schema: Schema;
  acceptedProviders?: readonly string[];
  name:
    | "provider"
    | "model"
    | "resolution"
    | "aspect_ratio"
    | "seconds"
    | "audio"
    | "max_cost_usd"
    | "timeout_seconds";
  value: unknown;
  node: NodeInstance;
  disabled?: boolean;
  error?: string;
  updateNodeConfig: (nodeId: Uuid, config: Record<string, unknown>) => void;
}

/** Catalog-backed provider and model choices for a media generation node. */
export function MediaModelField({
  definitionId,
  schema,
  acceptedProviders,
  name,
  value,
  node,
  disabled,
  error,
  updateNodeConfig,
}: MediaModelFieldProps) {
  const t = useTranslations("pages.workflows");
  const images = useImageProviders();
  const videos = useVideoProviders();
  const imageNode = definitionId === "image.generate";
  const rawProviders = imageNode ? images.providers : videos.providers;
  const accepted = schema["enum"];
  const providerAllowlist =
    acceptedProviders ??
    (Array.isArray(accepted)
      ? accepted.filter((item): item is string => typeof item === "string")
      : undefined);
  const providers = rawProviders
    .filter(
      (provider) => providerAllowlist === undefined || providerAllowlist.includes(provider.provider),
    )
    .map((provider) => ({
      id: provider.provider,
      name: provider.name,
      models: provider.models.map((model) => ({
        id: model.id,
        name: model.name,
        description: model.description,
        resolutions: "resolutions" in model ? model.resolutions : [],
        aspect_ratios: "aspect_ratios" in model ? model.aspect_ratios : [],
        audio: "audio" in model ? model.audio : "never",
        first_frame: "first_frame" in model ? model.first_frame : false,
        min_seconds: "min_seconds" in model ? model.min_seconds : 0,
        max_seconds: "max_seconds" in model ? model.max_seconds : 0,
        duration_choosable: "duration_choosable" in model ? model.duration_choosable : false,
        prices: "prices" in model ? model.prices ?? [] : [],
      })),
    }));
  const selectedProvider = providers.find((provider) => provider.id === node.config["provider"]);
  const selectedModel = selectedProvider?.models.find((model) => model.id === node.config["model"]);
  const videoChoices =
    name === "resolution"
      ? (selectedModel?.resolutions ?? []).map((id) => ({ id, name: id, description: "" }))
      : name === "aspect_ratio"
        ? (selectedModel?.aspect_ratios ?? []).map((id) => ({ id, name: id, description: "" }))
        : name === "audio" && selectedModel?.audio === "optional"
          ? [
              { id: "true", name: t("mediaAudioOn"), description: "" },
              { id: "false", name: t("mediaAudioOff"), description: "" },
            ]
          : [];
  const choices =
    name === "provider"
      ? providers
      : name === "resolution" || name === "aspect_ratio" || name === "audio"
        ? videoChoices
        : (selectedProvider?.models ?? []);
  const loading = imageNode ? images.isLoading : videos.isLoading;
  const failed = imageNode ? images.isError : videos.isError;
  const id = `workflow-media-${node.id}-${name}`;
  const label =
    name === "provider"
      ? t("mediaProvider")
      : name === "model"
        ? t("mediaModel")
        : t(`mediaField.${name}`);

  function choose(next: string) {
    const config = { ...node.config };
    if (name === "provider") {
      const chosen = providers.find((provider) => provider.id === next);
      config["provider"] = next;
      config["model"] = chosen?.models[0]?.id;
      delete config["secret_id"];
      delete config["resolution"];
      delete config["aspect_ratio"];
      delete config["seconds"];
      delete config["audio"];
    } else if (name === "model") {
      config["model"] = next;
      delete config["resolution"];
      delete config["aspect_ratio"];
      delete config["seconds"];
      delete config["audio"];
    } else if (name === "audio") {
      config["audio"] = next === "true";
    } else {
      config[name] = ["seconds", "max_cost_usd", "timeout_seconds"].includes(name)
        ? Number(next)
        : next;
    }
    updateNodeConfig(node.id, config);
  }

  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      {["seconds", "max_cost_usd", "timeout_seconds"].includes(name) ? (
        <Input
          id={id}
          aria-label={label}
          type="number"
          min={
            name === "seconds"
              ? (selectedModel?.min_seconds ?? 1)
              : name === "timeout_seconds"
                ? 60
                : 0.000001
          }
          max={
            name === "seconds"
              ? (selectedModel?.max_seconds ?? 180)
              : name === "timeout_seconds"
                ? 1800
                : undefined
          }
          step={name === "max_cost_usd" ? 0.000001 : 1}
          value={typeof value === "number" || typeof value === "string" ? value : ""}
          disabled={disabled || (name === "seconds" && !selectedModel?.duration_choosable)}
          onChange={(event) => {
            const raw = event.currentTarget.value;
            if (raw === "") updateNodeConfig(node.id, { ...node.config, [name]: undefined });
            else choose(raw);
          }}
        />
      ) : (
        <Select
          value={
            name === "audio"
              ? value === true
                ? "true"
                : value === false
                  ? "false"
                  : ""
              : typeof value === "string"
                ? value
                : ""
          }
          onValueChange={choose}
          disabled={disabled || loading || choices.length === 0}
        >
          <SelectTrigger id={id} aria-label={label}>
            <SelectValue
              placeholder={
                name === "provider" ? t("mediaProviderPlaceholder") : t("mediaModelPlaceholder")
              }
            />
          </SelectTrigger>
          <SelectContent>
            {choices.map((choice) => (
              <SelectItem key={choice.id} value={choice.id}>
                <span className="flex flex-col items-start">
                  <span>{choice.name}</span>
                  {name === "model" && choice.description && (
                    <span className="text-muted-foreground text-xs">{choice.description}</span>
                  )}
                </span>
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      )}
      {failed && <p className="text-destructive text-xs">{t("mediaCatalogUnavailable")}</p>}
      {error !== undefined && <p className="text-destructive text-xs">{error}</p>}
      {name === "model" && definitionId === "video.generate" && selectedModel && (
        <VideoEstimate model={selectedModel} config={node.config} />
      )}
    </div>
  );
}

function VideoEstimate({
  model,
  config,
}: {
  model: VideoModel;
  config: NodeInstance["config"];
}) {
  const t = useTranslations("pages.workflows");
  const today = new Date().toISOString().slice(0, 10);
  const current = (model.prices ?? [])
    .filter((price) => price.since <= today)
    .sort((left, right) => left.since.localeCompare(right.since))
    .at(-1);
  const resolution = typeof config["resolution"] === "string" ? config["resolution"] : model.resolutions[0];
  const seconds = !model.duration_choosable
    ? model.max_seconds
    : typeof config["seconds"] === "number"
      ? config["seconds"]
      : Math.min(5, model.max_seconds);
  const rate = current?.usd_per_second[resolution];
  const estimate = typeof rate === "string" || typeof rate === "number" ? Number(rate) * seconds : null;
  if (estimate === null || !Number.isFinite(estimate)) {
    return <p className="text-muted-foreground text-xs">{t("videoEstimateUnavailable")}</p>;
  }
  const amount = new Intl.NumberFormat(undefined, { style: "currency", currency: "USD" }).format(estimate);
  return (
    <p className="text-muted-foreground text-xs">
      {t("videoEstimate", { amount, seconds, resolution, since: current?.since ?? today })}
    </p>
  );
}
