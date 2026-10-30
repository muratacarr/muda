import { useQuery } from "@tanstack/react-query";
import { Result, Spin } from "antd";

import { fetchSite } from "../api";
import { Site } from "../components/Site";

export function Home() {
  const { data, isPending, isError } = useQuery({ queryKey: ["site"], queryFn: fetchSite });
  if (isPending) return <Spin fullscreen />;
  if (isError) {
    return (
      <Result
        status="warning"
        title="We could not load the menu right now."
        subTitle="Please try again in a moment."
      />
    );
  }
  return <Site content={data.content} />;
}
