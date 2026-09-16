import { Outlet } from "react-router-dom";

import { CallPanel } from "@/softphone/call-panel";
import { SoftphoneProvider } from "@/softphone/provider";

/** Wraps every page of the trainee with the softphone and its floating call panel. */
export function StudentFrame() {
  return (
    <SoftphoneProvider>
      <Outlet />
      <CallPanel />
    </SoftphoneProvider>
  );
}
