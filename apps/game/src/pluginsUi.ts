import type { PluginDescriptor } from "./plugins";

export function pluginLabel(p: PluginDescriptor): string {
  const v = p.version ? ` v${p.version}` : "";
  const src = p.source === "user" ? "user" : "built-in";
  return `${p.name}${v} (${src})`;
}
