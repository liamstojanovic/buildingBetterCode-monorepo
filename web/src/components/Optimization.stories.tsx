import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn, userEvent, within } from "storybook/test";
import { Optimization } from "./Optimization";
import { advisorChallenge, advisorDemo } from "../fixtures/advisor";

const meta = {
  title: "Canopy/Optimization",
  component: Optimization,
  args: {
    report: advisorDemo,
    origin: "demo",
    busy: false,
    error: null,
    onImport: fn(),
    onLoadDemo: fn(),
  },
  parameters: {
    docs: {
      description: {
        component:
          "Actual seven-group synthetic advisor analysis. Challenge variants are explicitly illustrative frontend fixtures; no model quality or savings is established.",
      },
    },
  },
} satisfies Meta<typeof Optimization>;
export default meta;
type Story = StoryObj<typeof meta>;

export const Empty: Story = { args: { report: null, origin: null } };
export const NativeCandidate: Story = {
  play: async ({ canvasElement }) => {
    await userEvent.click(
      within(canvasElement).getByRole("button", { name: /review_operations/ }),
    );
  },
};
export const MappingGaps: Story = {};
export const RetainedGeneration: Story = {
  play: async ({ canvasElement }) => {
    await userEvent.click(
      within(canvasElement).getByRole("button", { name: /write_response/ }),
    );
  },
};
export const Unsupported: Story = {
  play: async ({ canvasElement }) => {
    await userEvent.selectOptions(
      within(canvasElement).getByRole("combobox", {
        name: "Filter advisor status",
      }),
      "unsupported",
    );
  },
};
export const UnknownMeasurements: Story = {
  args: { report: advisorChallenge("unknown") },
};
export const PartialAnalysis: Story = {
  args: { report: advisorChallenge("partial") },
};
export const MixedOriginAndRetainedWork: Story = {
  args: { report: advisorChallenge("mixed") },
};
export const ImportFailure: Story = {
  args: { error: "Unsupported advisor version. Previous report preserved." },
};
