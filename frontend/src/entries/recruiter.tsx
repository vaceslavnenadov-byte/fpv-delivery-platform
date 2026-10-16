import { Route, Routes } from "react-router";
import { APPS } from "../apps/config";
import { ShellOutlet } from "../apps/shell";
import { Alert } from "../components/ui";
import { ProfilePage } from "../pages/Profile";
import { RecruiterOperatorsPage, RecruiterProfilesPage, RecruiterUsersPage } from "../pages/recruiter/RecruiterPages";
import { mountApp } from "./common";

mountApp(APPS.recruiter, (
  <Routes>
    <Route element={<ShellOutlet app={APPS.recruiter} />}>
      <Route path="/" element={<RecruiterUsersPage />} />
      <Route path="/profiles" element={<RecruiterProfilesPage />} />
      <Route path="/operators" element={<RecruiterOperatorsPage />} />
      <Route path="/profile" element={<ProfilePage />} />
      <Route path="*" element={<Alert tone="warn">Страница не найдена</Alert>} />
    </Route>
  </Routes>
));
