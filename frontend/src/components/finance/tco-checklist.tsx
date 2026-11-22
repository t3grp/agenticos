"use client";

import { useTranslations } from "next-intl";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui";

/**
 * What running this actually costs, as a checklist rather than a figure.
 *
 * Model spend is the only line this product meters, and it is the middle
 * one. Hosting is billed by whoever runs the server, and operator time — the
 * hours spent approving, onboarding and keeping keys and models current — is
 * billed by nobody and counted by no meter. A budget holder who prices only
 * the metered line under-prices the deployment, so the page names all three
 * and says where each number lives, or that it has none.
 */
export function TcoChecklist() {
  const t = useTranslations("pages.finance");
  const items = [
    { title: t("tcoHostTitle"), body: t("tcoHostBody") },
    { title: t("tcoModelsTitle"), body: t("tcoModelsBody") },
    { title: t("tcoOperatorTitle"), body: t("tcoOperatorBody") },
  ];
  return (
    <Card>
      <CardHeader className="space-y-1 px-5 pt-4 pb-2">
        <CardTitle className="text-sm">{t("tcoTitle")}</CardTitle>
        <CardDescription className="text-xs">{t("tcoDescription")}</CardDescription>
      </CardHeader>
      <CardContent className="px-5 pb-4">
        <ul className="space-y-3">
          {items.map((item) => (
            <li key={item.title} className="space-y-0.5">
              <p className="text-sm font-medium">{item.title}</p>
              <p className="text-muted-foreground text-sm">{item.body}</p>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
