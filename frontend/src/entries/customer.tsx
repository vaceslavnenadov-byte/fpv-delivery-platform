import { Route, Routes } from "react-router";
import { APPS } from "../apps/config";
import { ShellOutlet } from "../apps/shell";
import { Alert } from "../components/ui";
import { OrderDetailPage } from "../pages/OrderDetail";
import { ProfilePage } from "../pages/Profile";
import { CustomerOrdersPage } from "../pages/customer/CustomerOrders";
import { NewOrderPage } from "../pages/customer/NewOrder";
import { supportRoutes } from "../pages/support/routes";
import { mountApp } from "./common";

mountApp(APPS.customer, (
  <Routes>
    <Route element={<ShellOutlet app={APPS.customer} />}>
      <Route path="/" element={<CustomerOrdersPage />} />
      <Route path="/new" element={<NewOrderPage />} />
      <Route path="/orders/:id" element={<OrderDetailPage />} />
      <Route path="/profile" element={<ProfilePage />} />
      {supportRoutes()}
      <Route path="*" element={<Alert tone="warn">Страница не найдена</Alert>} />
    </Route>
  </Routes>
));
