import { pluginLabel } from "../src/pluginsUi";
import type { PluginDescriptor } from "../src/plugins";

describe("pluginsUi", () => {
  it("renders user plugin label with version", () => {
    const p: PluginDescriptor = {
      source: "user",
      id: "x",
      name: "My Plugin",
      version: "1.2.3",
      pluginPath: "C:/plugins/x",
    };
    expect(pluginLabel(p)).toBe("My Plugin v1.2.3 (user)");
  });

  it("renders built-in label without version", () => {
    const p: PluginDescriptor = {
      source: "builtin",
      id: "viz-beats",
      name: "Beats",
      packageName: "@auralprimer/viz-beats",
    };
    expect(pluginLabel(p)).toBe("Beats (built-in)");
  });
});
