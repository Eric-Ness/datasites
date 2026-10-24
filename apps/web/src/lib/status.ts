// Dataset freshness states, as apps/api/src/status.ts computes them.

export const STATUS_INFO: Record<string, { label: string; meaning: string }> = {
  ok: { label: "OK", meaning: "Checked on schedule, and the source is current." },
  pending: { label: "Pending", meaning: "Published, but not checked by the scheduler yet." },
  late: { label: "Late", meaning: "Our check is overdue: more than twice the dataset's check interval has passed." },
  "stale-upstream": {
    label: "Stale upstream",
    meaning:
      "The source hasn't published new data within the expected window. Our pipeline is working; we are waiting on the source.",
  },
  failing: { label: "Failing", meaning: "The last three or more runs failed. The last good version is still served." },
  blocked: {
    label: "Blocked",
    meaning: "The newest data failed validation, so it was quarantined. The last good version is still served.",
  },
  discontinued: { label: "Discontinued", meaning: "No longer updated. The final version stays available." },
  unpublished: { label: "Not published yet", meaning: "Listed on this website, but no version has been published yet." },
};

export function statusLabel(status: string): string {
  return STATUS_INFO[status]?.label ?? status;
}
