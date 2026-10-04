import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { CapabilitySwitches } from "../CapabilitySwitches";

const levels = {
  sandbox: ["strict", "standard", "host_dev"],
  web: ["off", "search", "search+fetch"],
  shell: ["readonly", "project", "ask"],
  packages: ["off", "via_it_agent"],
  subagents: ["off", "explore_only", "full"],
  browser: ["off", "screenshot"],
  vision: ["off", "on"],
};

const builder = {
  sandbox: "standard",
  web: "off",
  shell: "project",
  packages: "via_it_agent",
  subagents: "explore_only",
  browser: "off",
  vision: "off",
};

const WARNING = "DEV MODE: opencode agent running on host.";

function setup(props: Partial<React.ComponentProps<typeof CapabilitySwitches>> = {}) {
  const onChange = vi.fn();
  render(
    <CapabilitySwitches
      role="dev"
      roleType="builder"
      value={builder}
      onChange={onChange}
      levels={levels}
      defaults={builder}
      hostDevWarning={WARNING}
      {...props}
    />,
  );
  return onChange;
}

describe("CapabilitySwitches", () => {
  it("renders the role type and all seven toggles", () => {
    setup();
    expect(screen.getByText("builder")).toBeTruthy();
    for (const name of Object.keys(levels)) {
      expect(screen.getByLabelText(`dev ${name}`)).toBeTruthy();
    }
    expect((screen.getByLabelText("dev shell") as HTMLSelectElement).value).toBe("project");
  });

  it("calls onChange with the whole block when a toggle changes", () => {
    const onChange = setup();
    fireEvent.change(screen.getByLabelText("dev web"), { target: { value: "search" } });
    expect(onChange).toHaveBeenCalledWith({ ...builder, web: "search" });
  });

  it("disables vision with a tooltip when the model has no image input", () => {
    setup({ visionAvailable: false });
    const vision = screen.getByLabelText("dev vision") as HTMLSelectElement;
    expect(vision.disabled).toBe(true);
    expect(vision.title).toBe("Model has no image input");
  });

  it("keeps vision enabled when available", () => {
    setup({ visionAvailable: true });
    expect((screen.getByLabelText("dev vision") as HTMLSelectElement).disabled).toBe(false);
  });

  it("offers host_dev outside production and shows the warning once selected", () => {
    setup({ value: { ...builder, sandbox: "host_dev" } });
    const opt = screen.getByRole("option", { name: "host_dev" }) as HTMLOptionElement;
    expect(opt.disabled).toBe(false);
    expect(screen.getByRole("alert").textContent).toContain(WARNING);
  });

  it("disables the host_dev option in production", () => {
    setup({ production: true });
    const opt = screen.getByRole("option", { name: /host_dev/ }) as HTMLOptionElement;
    expect(opt.disabled).toBe(true);
  });

  it("resets to the role defaults", () => {
    const onChange = setup({ value: { ...builder, web: "search+fetch" } });
    fireEvent.click(screen.getByText("reset to defaults"));
    expect(onChange).toHaveBeenCalledWith(builder);
  });

  it("shows a validation error", () => {
    setup({ error: "web: 'x' is not valid" });
    expect(screen.getByRole("alert").textContent).toContain("web: 'x' is not valid");
  });
});
