import type { Preview } from "@storybook/react-vite";
import { initialize, mswLoader } from "msw-storybook-addon";
import "@xyflow/react/dist/style.css";
import "../src/styles.css";
import "../src/demo.css";

initialize({ onUnhandledRequest: "bypass" });
const preview: Preview = {
  loaders: [mswLoader],
  globalTypes: {
    theme: {
      name: "Theme",
      description: "Vice City color scheme",
      defaultValue: "system",
      toolbar: {
        icon: "mirror",
        items: [
          { value: "system", title: "System" },
          { value: "light", title: "Light" },
          { value: "dark", title: "Dark" },
        ],
        dynamicTitle: true,
      },
    },
  },
  decorators: [
    (Story, context) => {
      const theme = context.globals.theme;
      if (theme === "light" || theme === "dark")
        document.documentElement.dataset.theme = theme;
      else delete document.documentElement.dataset.theme;
      return Story();
    },
  ],
  parameters: {
    layout: "fullscreen",
    backgrounds: {
      default: "vice-day",
      values: [
        { name: "vice-day", value: "#fdf6ea" },
        { name: "vice-night", value: "#0c0d1b" },
      ],
    },
    a11y: { test: "error" },
  },
};
export default preview;
