package io.craftmcp.debugger;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.Strictness;
import com.google.gson.stream.JsonReader;
import com.google.gson.stream.JsonToken;
import java.io.StringReader;
import java.util.Set;

public final class Main {
    public static void main(String[] args) {
        int exit = run();
        if (exit != 0) System.exit(exit);
    }

    private static int run() {
        ProtocolIO io = new ProtocolIO(System.in, System.out);
        DebuggerEngine engine = new DebuggerEngine(io::write);
        Thread cleanup = new Thread(engine::close, "debugger-shutdown");
        Runtime.getRuntime().addShutdownHook(cleanup);
        try {
            engine.ready();
            String line;
            while ((line = io.readLine()) != null) {
                JsonReader reader = new JsonReader(new StringReader(line));
                reader.setStrictness(Strictness.STRICT);
                JsonElement parsed = Json.GSON.fromJson(reader, JsonElement.class);
                if (parsed == null || !parsed.isJsonObject() || reader.peek() != JsonToken.END_DOCUMENT)
                    throw new IllegalArgumentException("Expected a single request object.");
                JsonObject request = parsed.getAsJsonObject();
                Params envelope = new Params(request, Set.of("protocol_version", "type", "id", "method", "params"), Set.of());
                String id = envelope.string("id");
                if (envelope.integer("protocol_version", 1, 1) != 1 || !envelope.string("type").equals("request"))
                    throw new IllegalArgumentException("Unsupported protocol envelope.");
                String method = envelope.string("method");
                try {
                    if (!request.get("params").isJsonObject()) throw Params.invalid("params must be an object.");
                    JsonObject result = engine.call(method, request.getAsJsonObject("params"));
                    io.write(Json.object("protocol_version", 1, "type", "response", "id", id, "ok", true, "result", result));
                    if (method.equals("shutdown")) break;
                } catch (Fault fault) {
                    io.write(Json.object("protocol_version", 1, "type", "response", "id", id, "ok", false, "error", fault.json()));
                }
            }
            return engine.closeSafely() ? 0 : 1;
        } catch (Exception exception) {
            System.err.println("Debugger transport closed: " + exception.getClass().getSimpleName());
            return 2;
        } finally {
            engine.close();
            Runtime.getRuntime().removeShutdownHook(cleanup);
        }
    }

    private Main() {}
}
