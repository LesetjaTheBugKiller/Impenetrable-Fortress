//+------------------------------------------------------------------+
//| SignalSenderEA.mq5 |
//| Simple and reliable version with button feedback |
//+------------------------------------------------------------------+
#property strict

// ---- Inputs ----
input string ServerURL = "http://127.0.0.1:5001";  // Base URL, same as the receiver's BaseURL
input int TimeoutMs = 3000; // Short so a slow server does not freeze the panel and queue clicks
input int RepeatClickGuardMs = 1500; // BUY/SELL clicks this soon after the last send are ignored
input string AuthToken = ""; // must match Flask AUTH_SHARED if used
input double LotsToSend = 0.10;
input double RiskPerTradePct = 1.0; // same risk the receiver uses for the day target
input bool UseOverrideSymbol = true;
input string OverrideSymbol = "GER40Cash";

// Warm-up (pre-heat Lichess cache)
input bool EnableWarmup = true; // auto warm-up on attach
input int WarmupRetrySecs = 15; // retry every N secs until cache is warm
input int WarmupGiveUpSecs = 60; // stop trying after N secs (0 = never)
input bool EnableDebugLogging = true; // Enhanced debugging
input bool EnableSounds = true; // Click, accept, block, and warmup sounds

// ---- UI ----
#define BTN_BUY "BTN_SEND_BUY"
#define BTN_SELL "BTN_SEND_SELL"
#define BTN_WARM "BTN_WARM_UP"
#define BTN_TOGGLE "SND_TOGGLE"

// ---- State ----
datetime g_warmupStart = 0;
datetime g_lastWarmupAttempt = 0;
bool g_warmDone = false;
string g_lastWarmupResult = "";
string g_lastBoard = "";
bool g_panelOpen = true;
ulong g_lastSignalDoneMs = 0;

// ---- Tiny JSON helpers ----
int _Find(const string s,const string pat,const int from=0){ return StringFind(s,pat,from); }

string JStr(const string json,const string k){
  string pat="\""+k+"\":\""; int p=_Find(json,pat); if(p<0) return "";
  p+=(int)StringLen(pat); int n=(int)StringLen(json); int end=-1;
  for(int i=p;i<n;i++){ uchar c=(uchar)json[i]; if(c=='\"'){ if(i>0&&(uchar)json[i-1]=='\\') continue; end=i; break; } }
  if(end<0||end<=p) return ""; return StringSubstr(json,p,end-p);
}

string JBool(const string json,const string k){
  string pat="\""+k+"\":";
  int p=_Find(json,pat); if(p<0) return "";
  p+=(int)StringLen(pat); int n=(int)StringLen(json);
  
  // Skip whitespace
  while(p < n && (json[p] == ' ' || json[p] == '\t' || json[p] == '\r' || json[p] == '\n')) p++;
  
  if(p+4<=n && StringSubstr(json,p,4)=="true") return "true";
  if(p+5<=n && StringSubstr(json,p,5)=="false") return "false";
  return "";
}

string JNum(const string json,const string k){
  string pat="\""+k+"\":"; int p=_Find(json,pat); if(p<0) return "";
  p+=(int)StringLen(pat); int n=(int)StringLen(json);
  while(p<n && (uchar)json[p]==' ') p++;
  int end=p; while(end<n){ uchar c=(uchar)json[end]; if(c==','||c=='}'||c==' '||c=='\r'||c=='\n'||c=='\t') break; end++; }
  if(end<=p) return ""; 
  string raw=StringSubstr(json,p,end-p);
  StringReplace(raw,"\r",""); StringReplace(raw,"\n",""); StringReplace(raw,"\t","");
  return raw;
}

// ---- HTTP ----
bool PostJSONRaw(const string url,const string json,string &resp_body,string &resp_hdrs,bool verbose=true)
{
  string headers="Connection: close\r\nContent-Type: application/json\r\n";
  if(StringLen(AuthToken)>0) headers+="X-Auth-Token: "+AuthToken+"\r\n";

  uchar body[]; ArrayResize(body,(int)StringLen(json));
  StringToCharArray(json,body,0,(int)StringLen(json),CP_UTF8);

  uchar result[]; resp_hdrs=""; ResetLastError();
  int status=WebRequest("POST",url,headers,TimeoutMs,body,result,resp_hdrs);
  int le=GetLastError();
  resp_body=CharArrayToString(result,0,WHOLE_ARRAY,CP_UTF8);

  if(verbose || le != 0 || status != 200)
  {
    PrintFormat("[Sender] HTTP=%d lastError=%d url=%s", status, le, url);
    PrintFormat("[Sender] Request: %s", json);
    if(StringLen(resp_body) > 0) PrintFormat("[Sender] Response: %s", resp_body);
    
    if(le == 4014) {
      Print("[Sender] ERROR: URL not in allowed list. Add it in Tools->Options->Expert Advisors");
    }
  }

  return (status==200);
}

bool GetQuiet(const string url, string &resp_body)
{
  string headers = "Connection: close\r\n";
  if(StringLen(AuthToken) > 0) headers += "X-Auth-Token: " + AuthToken + "\r\n";
  uchar request[];
  uchar result[];
  string resp_hdrs;
  ResetLastError();
  int status = WebRequest("GET", url, headers, TimeoutMs, request, result, resp_hdrs);
  resp_body = CharArrayToString(result, 0, WHOLE_ARRAY, CP_UTF8);
  return status == 200;
}

color ToneColor(const string tone)
{
  if(tone == "blocked") return C'255,150,90';
  if(tone == "warn") return C'255,214,120';
  if(tone == "open") return C'120,220,150';
  return clrSilver;
}

void ShowObject(const string name, const bool visible);
void PaintDayBar(const double dayPct, const double lossCapPct, const double targetPct);

void SetLabel(const string name, const string text, const color ink)
{
  ObjectSetString(0, name, OBJPROP_TEXT, text);
  ObjectSetInteger(0, name, OBJPROP_COLOR, ink);
}

void RefreshTradeRisk()
{
  string body;
  if(!GetQuiet(ServerURL + "/trade-risk", body))
  {
    if(g_lastBoard != "offline")
    {
      g_lastBoard = "offline";
      SetLabel("SND_MINI", "No reply", C'255,150,90');
      SetLabel("SND_GATE", "No reply", C'255,150,90');
      SetLabel("SND_GATE_DETAIL", "The sender cannot reach the server", clrSilver);
      ChartRedraw();
    }
    return;
  }
  string summary = JStr(body, "summary");
  string gate = JStr(body, "gate");
  string dayPct = JNum(body, "dayPct");
  string stamp = gate + "|" + JStr(body, "losses") + "|" + JStr(body, "cooldown") + "|" + summary
                 + "|" + JStr(body, "day") + "|" + dayPct;
  if(stamp == g_lastBoard)
    return;
  g_lastBoard = stamp;

  if(StringLen(gate) > 0)
  {
    string losses = JStr(body, "losses");
    string mini = gate;
    if(StringLen(losses) > 0)
      mini = gate + "    " + losses;
    SetLabel("SND_MINI", mini, ToneColor(JStr(body, "gateTone")));
    SetLabel("SND_GATE", gate, ToneColor(JStr(body, "gateTone")));
    SetLabel("SND_GATE_DETAIL", JStr(body, "gateDetail"), clrSilver);
    SetLabel("SND_LOSSES", JStr(body, "losses"), ToneColor(JStr(body, "lossesTone")));
    SetLabel("SND_LOSSES_DETAIL", JStr(body, "lossesDetail"), clrSilver);
    SetLabel("SND_SPIKES", "Spikes  " + JStr(body, "spikes"), clrWhite);
    SetLabel("SND_COOLDOWN", "Cooldown  " + JStr(body, "cooldown"), clrWhite);
    SetLabel("SND_DAY", "Day  " + JStr(body, "day"), clrWhite);
    PaintDayBar(StringToDouble(dayPct), StringToDouble(JNum(body, "lossCapPct")), StringToDouble(JNum(body, "targetPct")));
    SetLabel("SND_BE", "Break-even  " + JStr(body, "breakEven"), clrWhite);
    SetLabel("SND_SCHEDULE", "Schedule  " + JStr(body, "schedule"), clrWhite);
  }

  string lines[3];
  lines[0] = "No open risk";
  lines[1] = "";
  lines[2] = "";
  color ink = clrSilver;
  if(StringLen(summary) > 0)
  {
    ink = C'255,214,120';
    string parts[];
    int count = StringSplit(summary, '|', parts);
    int shown = 0;
    for(int index = 0; index < count && shown < 3; index++)
    {
      string part = parts[index];
      StringTrimLeft(part);
      StringTrimRight(part);
      if(StringLen(part) == 0)
        continue;
      lines[shown] = part;
      shown++;
    }
    if(shown == 0)
      lines[0] = summary;
  }
  for(int index = 0; index < 3; index++)
    SetLabel("SND_RISK_" + IntegerToString(index + 1), lines[index], ink);
  ChartRedraw();
}

// Send a real signal (buttons BUY/SELL)
bool PostSignal(const string side,const string sym,const double lots,string &out_body)
{
  string endpoint = ServerURL + "/signal";
  string lots_str=DoubleToString(lots,4); StringReplace(lots_str,",",".");
  string json=StringFormat("{\"side\":\"%s\",\"symbol\":\"%s\",\"lots\":%s}", side, sym, lots_str);

  string body, hdr;
  bool http_ok = PostJSONRaw(endpoint, json, body, hdr, true);
  out_body = body;
  return http_ok && StringFind(body,"\"ok\":true")>=0;
}

// NEW: Proper warmup using dedicated /warmup endpoint
bool PostWarmup()
{
  string endpoint = ServerURL + "/warmup";
  string json = "{}";  // Empty JSON for warmup
  
  string body, hdr;
  bool http_ok = PostJSONRaw(endpoint, json, body, hdr, EnableDebugLogging);
  
  if(!http_ok) {
    if(EnableDebugLogging) Print("[WARMUP] HTTP request failed");
    return false;
  }
  
  // Parse response
  string ready = JBool(body, "ready");
  string ok = JBool(body, "ok");
  string message = JStr(body, "message");
  string failed_conditions = JStr(body, "failed_conditions");
  string rating = JNum(body, "rating");
  string games = JNum(body, "games");
  string win_rate = JNum(body, "win_rate");
  
  // Store last result for display
  if(StringLen(message) > 0) {
    g_lastWarmupResult = message;
    if(StringLen(failed_conditions) > 0) {
      g_lastWarmupResult += " [" + failed_conditions + "]";
    }
    if(StringLen(rating) > 0 && StringLen(games) > 0 && StringLen(win_rate) > 0) {
      g_lastWarmupResult += StringFormat(" | R:%s G:%s W:%s%%", 
                                        rating, games, win_rate);
    }
  }
  
  if(EnableDebugLogging)
  {
    PrintFormat("  ready=%s, ok=%s", ready, ok);
    PrintFormat("  message=%s", message);
    PrintFormat("  failed_conditions=%s", failed_conditions);
    PrintFormat("  rating=%s, games=%s, win_rate=%s", rating, games, win_rate);
  }
  
  return (ready == "true" || ok == "true");
}

void PlayCue(const string file)
{
  if(EnableSounds)
    PlaySound(file);
}

void SetStatus(const string text, const color ink)
{
  string shown = text;
  if(StringLen(shown) > 96)
    shown = StringSubstr(shown, 0, 93) + "...";
  ObjectSetString(0, "SND_STATUS", OBJPROP_TEXT, shown);
  ObjectSetInteger(0, "SND_STATUS", OBJPROP_COLOR, ink);
}

string WarmupStatusText(const string fallback)
{
  if(StringLen(g_lastWarmupResult) == 0)
    return fallback;
  return g_lastWarmupResult;
}

string SignalSymbol()
{
  if(UseOverrideSymbol && StringLen(OverrideSymbol) > 0)
    return OverrideSymbol;
  return _Symbol;
}

// ---- Build & send (button action) ----
bool SendSignal(string side_text)
{
  string side=side_text; StringToUpper(side);
  if(side!="BUY" && side!="SELL"){ Print("Bad side: ", side_text); return false; }

  string sym = SignalSymbol();
  string body;
  SetStatus("Sending " + side + " " + sym + "...", clrGold);
  ChartRedraw();
  bool ok = PostSignal(side,sym,LotsToSend,body);

  if(ok){
    SetStatus(side + " " + sym + " accepted", side == "BUY" ? C'120,220,150' : C'255,170,170');
    PlayCue(side == "BUY" ? "buy.wav" : "sell.wav");
    return true;
  }

  string reason = JStr(body, "reason");
  if(StringLen(reason) == 0)
    reason = "Server did not accept the signal";
  SetStatus(reason, C'255,150,90');
  PlayCue(StringLen(body) == 0 ? "timeout.wav" : "warning.wav");
  return false;
}

// ---- Manual warm-up button action ----
void DoManualWarmup()
{
  bool ok = PostWarmup();
  if(ok){
    g_warmDone = true;
    SetStatus(WarmupStatusText("Lichess ready"), C'120,220,150');
    Print("[Sender] Manual warm-up: cache ready");
    PlayCue("ok.wav");
  }else{
    SetStatus(WarmupStatusText("Lichess not ready"), C'255,150,90');
    Print("[Sender] Manual warm-up: still not ready");
    PlayCue("timeout.wav");
  }
}

void StyleObject(const string name)
{
  ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_LOWER);
  ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
  ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
  ObjectSetInteger(0, name, OBJPROP_BACK, false);
}

void CreateLabel(const string name, const int x, const int y, const int size, const color ink, const string text)
{
  ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);
  StyleObject(name);
  ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
  ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
  ObjectSetInteger(0, name, OBJPROP_COLOR, ink);
  ObjectSetInteger(0, name, OBJPROP_FONTSIZE, size);
  ObjectSetString(0, name, OBJPROP_FONT, "Segoe UI");
  ObjectSetString(0, name, OBJPROP_TEXT, text);
}

double PlannedDayTargetPct()
{
  double risk = RiskPerTradePct > 0.0 ? RiskPerTradePct : 1.0;
  return 4.0 * (0.5 * 4.0 * risk - 0.5 * risk);
}

string TugBlocks(const int filled, const int cells, const bool fromRight)
{
  string out = "";
  int on = filled;
  if(on < 0) on = 0;
  if(on > cells) on = cells;
  for(int i = 0; i < cells; i++)
  {
    bool lit = fromRight ? (i >= cells - on) : (i < on);
    out += lit ? "#" : ".";
  }
  return out;
}

void PaintDayBar(const double dayPct, const double lossCapPct, const double targetPct)
{
  double cap = lossCapPct > 0.0 ? lossCapPct : 3.0;
  double target = targetPct > 0.0 ? targetPct : PlannedDayTargetPct();
  if(target <= 0.0) target = 6.0;
  bool profit = dayPct >= 0.0;
  double fraction = profit ? dayPct / target : (-dayPct) / cap;
  if(fraction < 0.0) fraction = 0.0;
  if(fraction > 1.0) fraction = 1.0;
  int cells = 14;
  int filled = (int)MathRound(fraction * cells);
  int leftFill = profit ? 0 : filled;
  int rightFill = profit ? filled : 0;
  string bar = TugBlocks(leftFill, cells, true) + " | " + TugBlocks(rightFill, cells, false);
  color barInk = dayPct > 0.0001 ? C'120,220,150' : dayPct < -0.0001 ? C'255,150,90' : clrSilver;

  string capText = DoubleToString(cap, 1);
  string tgtText = DoubleToString(target, 1);
  StringReplace(capText, ",", ".");
  StringReplace(tgtText, ",", ".");
  SetLabel("SND_TUG_LOSS", "Loss cap  " + capText + "%", C'255,160,150');
  SetLabel("SND_TUG_GAIN", "Target  " + tgtText + "%", C'140,220,160');
  SetLabel("SND_TUG_BAR", bar, barInk);
  SetLabel("SND_TUG_ZERO", "0", clrWhite);
}

void CreateButton(const string name, const string text, const int x, const color bgColor)
{
  ObjectCreate(0, name, OBJ_BUTTON, 0, 0, 0);
  StyleObject(name);
  ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
  ObjectSetInteger(0, name, OBJPROP_YDISTANCE, 62);
  ObjectSetInteger(0, name, OBJPROP_XSIZE, 148);
  ObjectSetInteger(0, name, OBJPROP_YSIZE, 46);
  ObjectSetInteger(0, name, OBJPROP_BGCOLOR, bgColor);
  ObjectSetInteger(0, name, OBJPROP_COLOR, clrWhite);
  ObjectSetInteger(0, name, OBJPROP_FONTSIZE, 12);
  ObjectSetString(0, name, OBJPROP_FONT, "Segoe UI");
  ObjectSetInteger(0, name, OBJPROP_BORDER_TYPE, BORDER_FLAT);
  ObjectSetInteger(0, name, OBJPROP_BORDER_COLOR, C'230,230,230');
  ObjectSetInteger(0, name, OBJPROP_STATE, false);
  ObjectSetInteger(0, name, OBJPROP_ZORDER, 10);
  ObjectSetString(0, name, OBJPROP_TEXT, text);
  ObjectSetString(0, name, OBJPROP_TOOLTIP, text);
}

void DeleteUi()
{
  ObjectDelete(0, BTN_BUY);
  ObjectDelete(0, BTN_SELL);
  ObjectDelete(0, BTN_WARM);
  ObjectDelete(0, "StatusLabel");
  ObjectDelete(0, "RiskLabel");
  for(int index = ObjectsTotal(0) - 1; index >= 0; index--)
  {
    string name = ObjectName(0, index);
    if(StringFind(name, "SND_") == 0)
      ObjectDelete(0, name);
  }
}

void ApplyPanelMode();

void BuildPanel()
{
  DeleteUi();

  ObjectCreate(0, "SND_PANEL", OBJ_RECTANGLE_LABEL, 0, 0, 0);
  StyleObject("SND_PANEL");
  ObjectSetInteger(0, "SND_PANEL", OBJPROP_XDISTANCE, 12);
  ObjectSetInteger(0, "SND_PANEL", OBJPROP_YDISTANCE, 528);
  ObjectSetInteger(0, "SND_PANEL", OBJPROP_XSIZE, 540);
  ObjectSetInteger(0, "SND_PANEL", OBJPROP_YSIZE, 520);
  ObjectSetInteger(0, "SND_PANEL", OBJPROP_BGCOLOR, C'32,56,112');
  ObjectSetInteger(0, "SND_PANEL", OBJPROP_BORDER_TYPE, BORDER_FLAT);
  ObjectSetInteger(0, "SND_PANEL", OBJPROP_COLOR, C'96,132,188');
  ObjectSetInteger(0, "SND_PANEL", OBJPROP_BORDER_COLOR, C'96,132,188');
  ObjectSetInteger(0, "SND_PANEL", OBJPROP_ZORDER, 0);

  string lots = DoubleToString(LotsToSend, 2);
  StringReplace(lots, ",", ".");
  CreateLabel("SND_TITLE", 28, 504, 12, clrWhite, SignalSymbol() + "    " + lots + " lots");
  CreateLabel("SND_GATE", 28, 476, 18, clrSilver, "Waiting");
  CreateLabel("SND_GATE_DETAIL", 28, 438, 9, clrSilver, "Waiting for the server");
  CreateLabel("SND_LOSSES", 28, 418, 20, clrWhite, "—");
  CreateLabel("SND_LOSSES_DETAIL", 28, 376, 10, clrSilver, "Losses left today");
  CreateLabel("SND_SPIKES", 28, 352, 10, clrWhite, "Spikes");
  CreateLabel("SND_COOLDOWN", 28, 330, 10, clrWhite, "Cooldown");
  CreateLabel("SND_DAY", 28, 308, 10, clrWhite, "Day");
  CreateLabel("SND_TUG_LOSS", 28, 286, 12, C'255,160,150', "Loss cap  3.0%");
  CreateLabel("SND_TUG_GAIN", 300, 286, 12, C'140,220,160', "Target  6.0%");
  CreateLabel("SND_TUG_BAR", 28, 258, 14, clrSilver, ".............. | ..............");
  ObjectSetString(0, "SND_TUG_BAR", OBJPROP_FONT, "Consolas");
  CreateLabel("SND_TUG_ZERO", 268, 232, 10, clrWhite, "0");
  CreateLabel("SND_BE", 28, 214, 10, clrWhite, "Break-even");
  CreateLabel("SND_SCHEDULE", 28, 192, 10, clrWhite, "Schedule");
  CreateLabel("SND_RISK_HEAD", 28, 166, 9, C'186,204,228', "OPEN RISK");
  CreateLabel("SND_RISK_1", 28, 146, 11, clrSilver, "No open risk");
  CreateLabel("SND_RISK_2", 28, 126, 11, clrSilver, "");
  CreateLabel("SND_RISK_3", 28, 106, 11, clrSilver, "");
  CreateLabel("SND_STATUS", 28, 86, 9, clrSilver, EnableWarmup ? "Warming Lichess..." : "Ready");

  CreateButton(BTN_BUY, "BUY", 28, C'22,140,80');
  CreateButton(BTN_SELL, "SELL", 186, C'176,52,52');
  CreateButton(BTN_WARM, "WARM UP", 344, C'36,96,170');
  CreateLabel("SND_MINI", 28, 52, 12, clrSilver, "Waiting");
  CreateButton(BTN_TOGGLE, "Show", 330, C'52,84,150');
  if(GlobalVariableCheck("SenderPanelOpen"))
    g_panelOpen = GlobalVariableGet("SenderPanelOpen") > 0.5;
  PaintDayBar(0.0, 3.0, PlannedDayTargetPct());
  ApplyPanelMode();
}

void ShowObject(const string name, const bool visible)
{
  ObjectSetInteger(0, name, OBJPROP_TIMEFRAMES, visible ? OBJ_ALL_PERIODS : OBJ_NO_PERIODS);
}

void MoveToggle(const int x, const int y, const int w, const int h, const string text)
{
  ObjectSetInteger(0, BTN_TOGGLE, OBJPROP_XDISTANCE, x);
  ObjectSetInteger(0, BTN_TOGGLE, OBJPROP_YDISTANCE, y);
  ObjectSetInteger(0, BTN_TOGGLE, OBJPROP_XSIZE, w);
  ObjectSetInteger(0, BTN_TOGGLE, OBJPROP_YSIZE, h);
  ObjectSetString(0, BTN_TOGGLE, OBJPROP_TEXT, text);
  ObjectSetString(0, BTN_TOGGLE, OBJPROP_TOOLTIP, text);
}

void ApplyPanelMode()
{
  bool open = g_panelOpen;
  ObjectSetInteger(0, "SND_PANEL", OBJPROP_XDISTANCE, 12);
  ObjectSetInteger(0, "SND_PANEL", OBJPROP_YDISTANCE, open ? 528 : 88);
  ObjectSetInteger(0, "SND_PANEL", OBJPROP_XSIZE, open ? 540 : 420);
  ObjectSetInteger(0, "SND_PANEL", OBJPROP_YSIZE, open ? 520 : 72);
  MoveToggle(open ? 448 : 330, open ? 496 : 70, open ? 88 : 76, open ? 32 : 36, open ? "Hide" : "Show");
  ObjectSetInteger(0, "SND_MINI", OBJPROP_XDISTANCE, 28);
  ObjectSetInteger(0, "SND_MINI", OBJPROP_YDISTANCE, 58);

  ShowObject("SND_MINI", !open);
  ShowObject("SND_TITLE", open);
  ShowObject("SND_GATE", open);
  ShowObject("SND_GATE_DETAIL", open);
  ShowObject("SND_LOSSES", open);
  ShowObject("SND_LOSSES_DETAIL", open);
  ShowObject("SND_SPIKES", open);
  ShowObject("SND_COOLDOWN", open);
  ShowObject("SND_DAY", open);
  ShowObject("SND_TUG_LOSS", open);
  ShowObject("SND_TUG_GAIN", open);
  ShowObject("SND_TUG_BAR", open);
  ShowObject("SND_TUG_ZERO", open);
  ShowObject("SND_BE", open);
  ShowObject("SND_SCHEDULE", open);
  ShowObject("SND_RISK_HEAD", open);
  ShowObject("SND_RISK_1", open);
  ShowObject("SND_RISK_2", open);
  ShowObject("SND_RISK_3", open);
  ShowObject("SND_STATUS", open);
  ShowObject(BTN_BUY, open);
  ShowObject(BTN_SELL, open);
  ShowObject(BTN_WARM, open);
  ChartRedraw();
}

// ---- Lifecycle ----
int OnInit()
{
  Print("Sender: Tools→Options→Expert Advisors→Allow WebRequest add ", ServerURL);
  BuildPanel();
  ChartRedraw();
  g_warmupStart = TimeCurrent();
  EventSetTimer(2);
  return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
  EventKillTimer();
  DeleteUi();
}

void OnTimer()
{
  RefreshTradeRisk();
  if(!EnableWarmup || g_warmDone)
    return;

  int retrySecs = WarmupRetrySecs < 5 ? 5 : WarmupRetrySecs;
  if(g_lastWarmupAttempt > 0 && (TimeCurrent() - g_lastWarmupAttempt) < retrySecs)
    return;
  g_lastWarmupAttempt = TimeCurrent();

  if(WarmupGiveUpSecs > 0 && (TimeCurrent() - g_warmupStart) >= WarmupGiveUpSecs)
  {
    g_warmDone = true;
    SetStatus("Lichess warmup stopped", C'255,150,90');
    Print("[Sender] Warmup: giving up (time budget exceeded)");
    PlayCue("timeout.wav");
    ChartRedraw();
    return;
  }
  
  bool ok = PostWarmup();
  if(ok)
  {
    g_warmDone = true;
    SetStatus(WarmupStatusText("Lichess ready"), C'120,220,150');
    Print("[Sender] Warmup: Lichess cache ready");
    PlayCue("ok.wav");
  }
  else
    SetStatus(WarmupStatusText("Warming Lichess..."), clrGold);
  ChartRedraw();
}

void PressButton(const string name, const color pressed)
{
  ObjectSetInteger(0, name, OBJPROP_BGCOLOR, pressed);
  ObjectSetInteger(0, name, OBJPROP_STATE, true);
  ChartRedraw();
  PlayCue("tick.wav");
}

void ReleaseButton(const string name, const color normal)
{
  ObjectSetInteger(0, name, OBJPROP_BGCOLOR, normal);
  ObjectSetInteger(0, name, OBJPROP_STATE, false);
  ChartRedraw();
}

void HandleButtonClick(const string buttonName)
{
  if(buttonName == BTN_TOGGLE)
  {
    g_panelOpen = !g_panelOpen;
    GlobalVariableSet("SenderPanelOpen", g_panelOpen ? 1.0 : 0.0);
    ApplyPanelMode();
    PlayCue("tick.wav");
    return;
  }
  if(!g_panelOpen)
    return;
  if((buttonName == BTN_BUY || buttonName == BTN_SELL) && g_lastSignalDoneMs > 0 &&
     GetTickCount64() - g_lastSignalDoneMs < (ulong)(RepeatClickGuardMs > 0 ? RepeatClickGuardMs : 0))
  {
    // Clicks queued while the panel was waiting on the server land here.
    ObjectSetInteger(0, buttonName, OBJPROP_STATE, false);
    SetStatus("Repeat click ignored", C'255,214,120');
    ChartRedraw();
    return;
  }
  if(buttonName == BTN_BUY)
  {
    PressButton(BTN_BUY, C'46,180,110');
    SendSignal("BUY");
    g_lastSignalDoneMs = GetTickCount64();
    ReleaseButton(BTN_BUY, C'22,140,80');
  }
  else if(buttonName == BTN_SELL)
  {
    PressButton(BTN_SELL, C'210,80,80');
    SendSignal("SELL");
    g_lastSignalDoneMs = GetTickCount64();
    ReleaseButton(BTN_SELL, C'176,52,52');
  }
  else if(buttonName == BTN_WARM)
  {
    PressButton(BTN_WARM, C'70,130,200');
    SetStatus("Warming Lichess...", clrGold);
    DoManualWarmup();
    ReleaseButton(BTN_WARM, C'36,96,170');
  }
}

void OnChartEvent(const int id, const long &lparam, const double &dparam, const string &sparam)
{
  if(id != CHARTEVENT_OBJECT_CLICK)
    return;
  if(sparam == BTN_BUY || sparam == BTN_SELL || sparam == BTN_WARM || sparam == BTN_TOGGLE)
    HandleButtonClick(sparam);
}