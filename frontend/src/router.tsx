import { createBrowserRouter } from "react-router-dom";
import AppLayout from "./layout/AppLayout";
import WorkspacePage from "./pages/WorkspacePage";
import ProjectPage from "./pages/ProjectPage";

export const router = createBrowserRouter([
  {
    path: "/",
    element: <AppLayout />,
    children: [
      { index: true, element: <WorkspacePage /> },
      { path: "project/:projectId", element: <ProjectPage /> },
    ],
  },
]);
