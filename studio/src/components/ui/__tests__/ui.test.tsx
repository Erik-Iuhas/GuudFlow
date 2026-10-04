import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { Badge } from "../badge";
import { Button } from "../button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "../card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "../dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "../dropdown-menu";
import { ScrollArea } from "../scroll-area";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "../sheet";
import { Switch } from "../switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../tabs";
import { Toast, ToastClose, ToastDescription, ToastProvider, ToastTitle, ToastViewport } from "../toast";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "../tooltip";
import { cn } from "../../../lib/utils";

describe("cn", () => {
  it("merges conflicting tailwind utilities, last wins", () => {
    expect(cn("px-2 py-1", "px-4")).toBe("py-1 px-4");
    expect(cn("a", false && "b", ["c"])).toBe("a c");
  });
});

describe("Button", () => {
  it("renders, handles clicks and defaults to type=button", () => {
    const onClick = vi.fn();
    render(<Button onClick={onClick}>Go</Button>);
    const btn = screen.getByRole("button", { name: "Go" });
    expect(btn).toHaveAttribute("type", "button");
    fireEvent.click(btn);
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("applies variant classes and respects disabled", () => {
    render(<Button variant="destructive" size="sm" disabled>Del</Button>);
    const btn = screen.getByRole("button", { name: "Del" });
    expect(btn).toBeDisabled();
    expect(btn.className).toContain("bg-destructive");
    expect(btn.className).toContain("h-8");
  });

  it("asChild renders the child element", () => {
    render(<Button asChild><a href="/x">Link</a></Button>);
    expect(screen.getByRole("link", { name: "Link" })).toHaveAttribute("href", "/x");
  });
});

describe("Card and Badge", () => {
  it("renders the card parts", () => {
    render(
      <Card data-testid="card">
        <CardHeader><CardTitle>Title</CardTitle><CardDescription>Desc</CardDescription></CardHeader>
        <CardContent>Body</CardContent>
        <CardFooter>Foot</CardFooter>
      </Card>,
    );
    expect(screen.getByTestId("card")).toHaveTextContent("TitleDescBodyFoot");
    expect(screen.getByRole("heading", { name: "Title" })).toBeInTheDocument();
  });

  it("badge status variants use status tokens", () => {
    render(<Badge variant="failed">failed</Badge>);
    expect(screen.getByText("failed").className).toContain("text-status-failed");
  });
});

describe("Dialog and Sheet", () => {
  it("opens a dialog from its trigger", () => {
    render(
      <Dialog>
        <DialogTrigger>Open</DialogTrigger>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Sure?</DialogTitle>
            <DialogDescription>This cannot be undone.</DialogDescription>
          </DialogHeader>
        </DialogContent>
      </Dialog>,
    );
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.click(screen.getByText("Open"));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText("Sure?")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("renders a sheet on the requested side", () => {
    render(
      <Sheet open>
        <SheetContent side="left">
          <SheetHeader><SheetTitle>Panel</SheetTitle><SheetDescription>Side panel</SheetDescription></SheetHeader>
        </SheetContent>
      </Sheet>,
    );
    const dialog = screen.getByRole("dialog");
    expect(dialog.className).toContain("left-0");
    expect(screen.getByText("Panel")).toBeInTheDocument();
  });
});

describe("Tabs", () => {
  it("shows the active panel and switches on activation", () => {
    render(
      <Tabs defaultValue="a">
        <TabsList>
          <TabsTrigger value="a">A</TabsTrigger>
          <TabsTrigger value="b">B</TabsTrigger>
        </TabsList>
        <TabsContent value="a">panel a</TabsContent>
        <TabsContent value="b">panel b</TabsContent>
      </Tabs>,
    );
    expect(screen.getByText("panel a")).toBeInTheDocument();
    expect(screen.queryByText("panel b")).toBeNull();
    fireEvent.mouseDown(screen.getByRole("tab", { name: "B" }), { button: 0, ctrlKey: false });
    expect(screen.getByText("panel b")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "B" })).toHaveAttribute("aria-selected", "true");
  });
});

describe("Tooltip", () => {
  it("shows its content when the trigger is focused", async () => {
    render(
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger>Hover me</TooltipTrigger>
          <TooltipContent>Helpful text</TooltipContent>
        </Tooltip>
      </TooltipProvider>,
    );
    fireEvent.focus(screen.getByText("Hover me"));
    expect(await screen.findByRole("tooltip")).toHaveTextContent("Helpful text");
  });
});

describe("Switch", () => {
  it("toggles and reports the new value", () => {
    const onChange = vi.fn();
    render(<Switch aria-label="notify" onCheckedChange={onChange} />);
    const sw = screen.getByRole("switch", { name: "notify" });
    expect(sw).not.toBeChecked();
    fireEvent.click(sw);
    expect(onChange).toHaveBeenCalledWith(true);
    expect(sw).toBeChecked();
  });

  it("does not toggle when disabled", () => {
    const onChange = vi.fn();
    render(<Switch aria-label="locked" disabled onCheckedChange={onChange} />);
    fireEvent.click(screen.getByRole("switch", { name: "locked" }));
    expect(onChange).not.toHaveBeenCalled();
  });
});

describe("ScrollArea", () => {
  it("renders its children", () => {
    render(<ScrollArea className="h-10"><p>scroll content</p></ScrollArea>);
    expect(screen.getByText("scroll content")).toBeInTheDocument();
  });
});

describe("DropdownMenu", () => {
  it("renders items and fires selection", () => {
    const onSelect = vi.fn();
    render(
      <DropdownMenu open>
        <DropdownMenuTrigger>Menu</DropdownMenuTrigger>
        <DropdownMenuContent>
          <DropdownMenuLabel>Actions</DropdownMenuLabel>
          <DropdownMenuSeparator />
          <DropdownMenuItem onSelect={onSelect}>Rename</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>,
    );
    expect(screen.getByText("Actions")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("menuitem", { name: "Rename" }));
    expect(onSelect).toHaveBeenCalled();
  });
});

describe("Toast", () => {
  it("renders title, description and a dismiss button", () => {
    const onOpenChange = vi.fn();
    render(
      <ToastProvider>
        <Toast open variant="error" onOpenChange={onOpenChange}>
          <ToastTitle>Oops</ToastTitle>
          <ToastDescription>Something broke</ToastDescription>
          <ToastClose />
        </Toast>
        <ToastViewport />
      </ToastProvider>,
    );
    expect(screen.getByText("Something broke")).toBeInTheDocument();
    expect(screen.getByText("Oops").closest("[data-variant]")).toHaveAttribute("data-variant", "error");
    fireEvent.click(screen.getByRole("button", { name: "dismiss" }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});
