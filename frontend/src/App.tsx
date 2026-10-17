import { RouterProvider } from "react-router-dom";
import { ThemeProvider } from "./features/theme/ThemeProvider";
import { router } from "./router";

export default function App() {
  return (
    <ThemeProvider>
      <RouterProvider router={router} />
    </ThemeProvider>
  );
}
