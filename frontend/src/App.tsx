import { Result } from "antd";
import { Route, Routes } from "react-router-dom";

import { Home } from "./pages/Home";
import { Preview } from "./pages/Preview";

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<Home />} />
      <Route path="/preview/:token" element={<Preview />} />
      <Route path="*" element={<Result status="404" title="Page not found" />} />
    </Routes>
  );
}
