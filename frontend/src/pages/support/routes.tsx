import { Route } from "react-router";
import { AdminSupportPage, SupportListPage, SupportNewPage, SupportTicketPage } from "./SupportPages";

/** Маршруты техподдержки — одинаковые во всех клиентах ролей. */
export function supportRoutes(admin = false) {
  return [
    <Route key="s" path="/support" element={<SupportListPage />} />,
    <Route key="sn" path="/support/new" element={<SupportNewPage />} />,
    <Route key="st" path="/support/:id" element={<SupportTicketPage />} />,
    ...(admin ? [<Route key="sa" path="/support-admin" element={<AdminSupportPage />} />] : []),
  ];
}
