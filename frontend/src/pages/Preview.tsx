import { useQuery } from "@tanstack/react-query";
import { Alert, Result, Segmented, Spin } from "antd";
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";

import { fetchPreview } from "../api";
import { Site } from "../components/Site";

function useNoIndex() {
  useEffect(() => {
    const meta = document.createElement("meta");
    meta.name = "robots";
    meta.content = "noindex, nofollow";
    document.head.appendChild(meta);
    return () => meta.remove();
  }, []);
}

type View = "Proposed" | "Current";

export function Preview() {
  useNoIndex();
  const { token = "" } = useParams();
  const [view, setView] = useState<View>("Proposed");
  const { data, isPending, isError } = useQuery({
    queryKey: ["preview", token],
    queryFn: () => fetchPreview(token),
    retry: false,
  });

  if (isPending) return <Spin fullscreen />;
  if (isError) {
    // 404 covers forged, expired and already-decided previews alike; the reason is not revealed.
    return <Result status="404" title="This preview has expired or is no longer available." />;
  }

  return (
    <div>
      <div className="sticky top-0 z-10 space-y-3 border-b border-gray-300 bg-white p-4 text-gray-900">
        <Alert type="info" showIcon message="Nothing is live until you approve it in Telegram." />
        <Segmented<View>
          options={["Proposed", "Current"]}
          value={view}
          onChange={setView}
        />
        <ul className="m-0 list-none space-y-1 p-0 text-sm">
          {data.changes.map((c) => (
            <li key={c.field}>
              <code>{c.field}</code>
              <span>{`: ${c.old} → ${c.new}`}</span>
            </li>
          ))}
        </ul>
      </div>
      <Site content={view === "Proposed" ? data.draft : data.published} />
    </div>
  );
}
