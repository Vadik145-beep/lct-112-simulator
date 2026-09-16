import { Outlet, useLocation } from "react-router-dom";

import { CallPanel } from "@/softphone/call-panel";
import { SoftphoneProvider } from "@/softphone/provider";

// The operator-112 card embeds the panel itself (wave 7); everywhere else it floats.
const CARD_ROUTE = /^\/student\/attempts\/[^/]+$/;

/** Wraps every page of the trainee with the softphone and its floating call panel. */
export function StudentFrame() {
  const { pathname } = useLocation();
  return (
    <SoftphoneProvider>
      <Outlet />
      {!CARD_ROUTE.test(pathname) && <CallPanel />}
    </SoftphoneProvider>
  );
}
