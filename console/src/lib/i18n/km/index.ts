import { app } from "./app";
import { nav } from "./nav";
import { common } from "./common";
import { auth } from "./auth";
import { cloud } from "./cloud";
import { pages } from "./pages";

export const km: Record<string, string> = {
  ...app,
  ...nav,
  ...common,
  ...auth,
  ...cloud,
  ...pages,
};
