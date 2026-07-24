import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import { Dialog, OutcomeBadge, SourceCard } from "../components";

function DialogHarness() {
  const [open, setOpen] = useState(false);

  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        Open preferences
      </button>
      <Dialog
        open={open}
        onClose={() => setOpen(false)}
        title="Study preferences"
      >
        <button type="button">Save preference</button>
      </Dialog>
    </>
  );
}

describe("shared accessible components", () => {
  it("opens native Dialog with focused content and restores focus after Escape", async () => {
    const user = userEvent.setup();
    render(<DialogHarness />);
    const opener = screen.getByRole("button", { name: "Open preferences" });

    await user.click(opener);

    const dialog = screen.getByRole("dialog", { name: "Study preferences" });
    await waitFor(() => expect(dialog.hasAttribute("open")).toBe(true));
    await waitFor(() =>
      expect(document.activeElement?.getAttribute("aria-label")).toBe(
        "Close dialog",
      ),
    );

    await user.keyboard("{Escape}");

    await waitFor(() => expect(dialog.hasAttribute("open")).toBe(false));
    expect(document.activeElement).toBe(opener);
  });

  it("distinguishes PDF pages from presentation slides in source lineage", () => {
    render(
      <div>
        <SourceCard
          source={{
            index: 1,
            filename: "chapter.pdf",
            mime_type: "application/pdf",
            document_id: "7",
            page_number: 12,
            chunk_index: 3,
            excerpt: "Page evidence excerpt.",
          }}
        />
        <SourceCard
          source={{
            index: 2,
            filename: "lecture.pptx",
            mime_type:
              "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            document_id: "8",
            notebook_id: "4",
            slide_number: 6,
            chunk_index: 1,
            excerpt: "Slide evidence excerpt.",
          }}
        />
      </div>,
    );

    const pageSource = screen.getByRole("article", {
      name: "Source 1: chapter.pdf",
    });
    const slideSource = screen.getByRole("article", {
      name: "Source 2: lecture.pptx",
    });

    expect(within(pageSource).getByText("Page 12")).toBeTruthy();
    expect(within(pageSource).queryByText(/Slide/)).toBeNull();
    expect(within(slideSource).getByText("Slide 6")).toBeTruthy();
    expect(within(slideSource).queryByText(/Page/)).toBeNull();
    expect(slideSource.textContent).toContain("Notebook 4");
  });

  it("communicates every learning outcome with text, icon, and semantic tone", () => {
    render(
      <div>
        <OutcomeBadge outcome="understood" />
        <OutcomeBadge outcome="partial" />
        <OutcomeBadge outcome="confused" />
        <OutcomeBadge outcome="unrated" />
      </div>,
    );

    const expectations = [
      ["Understood", "badge--success"],
      ["Partly understood", "badge--warning"],
      ["Needs review", "badge--danger"],
      ["Unrated", "badge--neutral"],
    ] as const;

    for (const [label, toneClass] of expectations) {
      const badge = screen.getByText(label).closest(".badge");
      expect(badge).not.toBeNull();
      expect(badge?.classList.contains(toneClass)).toBe(true);
      expect(badge?.querySelector("svg")?.getAttribute("aria-hidden")).toBe(
        "true",
      );
    }
  });
});
