//+------------------------------------------------------------------+
//| APEX_MT5_MarketBridge.mq5                                        |
//| Sends Market Watch bid/ask ticks to APEX. No trade functions.    |
//+------------------------------------------------------------------+
#property strict
#property version   "1.00"
#property description "APEX Forex market-data bridge. Does not place orders."

input string InpApexEndpoint = "https://apex-algo-backend.onrender.com/api/indian-forex/api/v1/mt5/ingest";
input string InpBridgeSecret = "SET_THE_SAME_MT5_BRIDGE_TOKEN";
input int    InpTimerMilliseconds = 500;
input int    InpRequestTimeoutMs = 5000;
input int    InpMaxSymbolsPerBatch = 100;

int OnInit()
{
   if(StringLen(InpApexEndpoint) < 12 || StringFind(InpApexEndpoint, "YOUR-APEX-HOST") >= 0)
   {
      Print("Configure InpApexEndpoint before attaching the EA.");
      return INIT_PARAMETERS_INCORRECT;
   }
   if(StringLen(InpBridgeSecret) < 24)
   {
      Print("Bridge secret must be at least 24 characters.");
      return INIT_PARAMETERS_INCORRECT;
   }
   EventSetMillisecondTimer(MathMax(250, InpTimerMilliseconds));
   Print("APEX MT5 market-data bridge started. Add the APEX host to Tools > Options > Expert Advisors > WebRequest allowlist.");
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
}

void OnTick()
{
   // Timer batches the full Market Watch, avoiding one HTTP request per tick.
}

void OnTimer()
{
   int total = SymbolsTotal(true);
   if(total <= 0)
      return;

   string json = "{\"ticks\":[";
   int sent = 0;
   for(int i = 0; i < total && sent < InpMaxSymbolsPerBatch; i++)
   {
      string symbol = SymbolName(i, true);
      if(symbol == "")
         continue;

      long calc_mode = SymbolInfoInteger(symbol, SYMBOL_TRADE_CALC_MODE);
      if(calc_mode != SYMBOL_CALC_MODE_FOREX && calc_mode != SYMBOL_CALC_MODE_FOREX_NO_LEVERAGE)
         continue;

      MqlTick tick;
      if(!SymbolInfoTick(symbol, tick) || tick.bid <= 0 || tick.ask <= 0 || tick.ask < tick.bid)
         continue;

      if(sent > 0)
         json += ",";
      json += StringFormat(
         "{\"symbol\":\"%s\",\"bid\":%.10f,\"ask\":%.10f,\"last\":%.10f,\"time_msc\":%I64d}",
         symbol, tick.bid, tick.ask, tick.last, tick.time_msc
      );
      sent++;
   }
   json += "]}";
   if(sent == 0)
      return;

   char data[];
   char result[];
   string result_headers;
   int copied = StringToCharArray(json, data, 0, WHOLE_ARRAY, CP_UTF8);
   if(copied > 0)
      ArrayResize(data, copied - 1);

   string headers = "Content-Type: application/json\r\nX-APEX-MT5-KEY: " + InpBridgeSecret + "\r\n";
   ResetLastError();
   int status = WebRequest(
      "POST", InpApexEndpoint, headers, InpRequestTimeoutMs,
      data, result, result_headers
   );
   if(status != 200)
      PrintFormat("APEX bridge HTTP status=%d lastError=%d", status, GetLastError());
}
//+------------------------------------------------------------------+
