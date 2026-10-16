import { Route, Routes } from "react-router";
import { APPS } from "../apps/config";
import { ShellOutlet } from "../apps/shell";
import { Alert } from "../components/ui";
import { OrderDetailPage } from "../pages/OrderDetail";
import { ProfilePage } from "../pages/Profile";
import { CustomerDronesPage } from "../pages/customer/CustomerDrones";
import { CustomerOrdersPage } from "../pages/customer/CustomerOrders";
import { NewOrderPage } from "../pages/customer/NewOrder";
import { mountApp } from "./common";

mountApp(APPS.customer, (
  <Routes>
    <Route element={<ShellOutlet app={APPS.customer} />}>
      <Route path="/" element={<CustomerOrdersPage />} />
      <Route path="/new" element={<NewOrderPage />} />
      <Route path="/drones" element={<CustomerDronesPage />} />
      <Route path="/orders/:id" element={<OrderDetailPage />} />
      <Route path="/profile" element={<ProfilePage />} />
      <Route path="*" element={<Alert tone="warn">Страница не найдена</Alert>} />
    </Route>
  </Routes>
));
