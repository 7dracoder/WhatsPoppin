import { useContext } from "react";
import { AssistantContext } from "./AssistantProvider";

export function useAssistant() {
  const ctx = useContext(AssistantContext);
  if (!ctx) throw new Error("useAssistant must be used within an AssistantProvider");
  return ctx;
}

/** The assistant, or null outside its provider (tests, isolated pages). */
export function useOptionalAssistant() {
  return useContext(AssistantContext);
}
