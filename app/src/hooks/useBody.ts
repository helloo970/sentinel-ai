import { useEffect } from "react";
import { handleGesture, presenceChanged } from "../lib/body";
import { onGesture, startVision, stopVision } from "../lib/vision";
import { store, useStore } from "../state/store";

/** Runs the camera and gestures while the app is open (can be turned off in settings). */
export function useBody(): void {
  const enabled = useStore((s) => s.cameraEnabled);
  const present = useStore((s) => s.camera.present);

  useEffect(() => {
    if (!enabled) return;
    onGesture(handleGesture);
    void startVision();
    return () => stopVision();
  }, [enabled]);

  useEffect(() => {
    if (!enabled) return;
    const status = store.get().status;
    presenceChanged(present, status?.user_name ?? "", status?.night ?? false);
  }, [present, enabled]);
}
