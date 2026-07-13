declare module "jsdom" {
  export interface JSDOMOptions {
    url?: string;
    runScripts?: "dangerously" | "outside-only";
    pretendToBeVisual?: boolean;
  }

  export class JSDOM {
    constructor(html?: string, options?: JSDOMOptions);
    window: Window & typeof globalThis;
  }
}
