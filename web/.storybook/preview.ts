import type { Preview } from "@storybook/react-vite";
import { initialize, mswLoader } from "msw-storybook-addon";
import "@xyflow/react/dist/style.css";
import "../src/styles.css";

initialize({ onUnhandledRequest: "bypass" });
const preview: Preview = {
  loaders: [mswLoader],
  parameters: {
    layout: "fullscreen",
    backgrounds: {
      default: "canopy",
      values: [{ name: "canopy", value: "#101815" }],
    },
    a11y: { test: "error" },
  },
};
export default preview;
