package edu.bca182.activity;
import java.time.Instant;
import org.json.JSONObject;
import org.json.JSONArray;
import org.junit.Test;
import static org.junit.Assert.*;
public class ThreeClassPayloadTest {
    private JSONObject current() throws Exception {
        return new JSONObject().put("device_id","test-device").put("state","STATIONARY")
            .put("activity","stationary").put("hardware_validated",true).put("mode","board_validated")
            .put("server_timestamp",Instant.now().toString()).put("prediction_schema_version",2)
            .put("model_id",ContractState.THREE_CLASS_MODEL_ID);
    }
    @Test public void actualPayloadParserHandlesStationaryCurrentAndHistory() throws Exception {
        JSONObject c=current();
        JSONObject item=new JSONObject(c.toString()).put("inference_mode","board_validated");
        ActivityPayload p=ActivityPayload.parse(c,new JSONObject().put("items",new JSONArray().put(item)),"test-device");
        assertEquals("STATIONARY",p.state);assertTrue(p.history.get(0).endsWith("\nSTATIONARY"));
        c.put("model_id","incorrect-model");
        assertEquals("UNVALIDATED",ActivityPayload.parse(c,new JSONObject().put("items",new JSONArray()),"test-device").state);
    }
    @Test public void versionMustBeStrictIntegerAndSourceMustMatch() throws Exception {
        JSONObject c=current().put("prediction_schema_version",2.0);
        try {ActivityPayload.parse(c,new JSONObject().put("items",new JSONArray()),"test-device");fail();}
        catch(IllegalArgumentException expected) { }
        try {ActivityPayload.parse(current(),new JSONObject().put("items",new JSONArray()),"wrong-device");fail();}
        catch(IllegalArgumentException expected) { }
    }
}
