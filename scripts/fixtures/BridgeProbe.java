package app.exteracontext.probe;

/** Keeps custom instances in Java; returns only bootstrap values to Python. */
public final class BridgeProbe {
    private static int calls;
    public static long marker() { return ++calls; }
    public static String dispatch(Runnable callback) {
        callback.run();
        return "java-dispatched";
    }
}
