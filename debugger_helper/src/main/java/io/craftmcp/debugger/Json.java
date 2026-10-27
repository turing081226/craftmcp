package io.craftmcp.debugger;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonObject;
import java.nio.charset.StandardCharsets;

final class Json {
    static final Gson GSON = new GsonBuilder().serializeNulls().disableHtmlEscaping().create();

    static JsonObject object(Object... pairs) {
        JsonObject result = new JsonObject();
        for (int i = 0; i < pairs.length; i += 2) result.add((String) pairs[i], GSON.toJsonTree(pairs[i + 1]));
        return result;
    }

    static int bytes(Object value) {
        return GSON.toJson(value).getBytes(StandardCharsets.UTF_8).length;
    }

    private Json() {}
}
