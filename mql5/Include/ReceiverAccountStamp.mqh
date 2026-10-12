#ifndef RECEIVER_ACCOUNT_STAMP_MQH
#define RECEIVER_ACCOUNT_STAMP_MQH

// Writes MQL5\Files\RX_account.txt for deploy.cmd account binding (login,
// mode, server, ts). It proves which account this terminal is on, not that
// the Receiver is trading. Written to a temp file first, then moved.

#define ACCOUNT_STAMP_FILE "RX_account.txt"
#define ACCOUNT_STAMP_TMP  "RX_account.tmp"

string AccountStampMode()
{
   long mode = AccountInfoInteger(ACCOUNT_TRADE_MODE);
   if(mode == ACCOUNT_TRADE_MODE_REAL) return "REAL";
   if(mode == ACCOUNT_TRADE_MODE_DEMO) return "DEMO";
   if(mode == ACCOUNT_TRADE_MODE_CONTEST) return "CONTEST";
   return "UNKNOWN";
}

bool WriteAccountStamp()
{
   int handle = FileOpen(ACCOUNT_STAMP_TMP, FILE_WRITE | FILE_TXT | FILE_ANSI);
   if(handle == INVALID_HANDLE)
   {
      PrintFormat("[RX] Account stamp: could not write %s (%d)", ACCOUNT_STAMP_TMP, GetLastError());
      return false;
   }
   FileWriteString(handle, StringFormat("login=%I64d\n", AccountInfoInteger(ACCOUNT_LOGIN)));
   FileWriteString(handle, "mode=" + AccountStampMode() + "\n");
   FileWriteString(handle, "server=" + AccountInfoString(ACCOUNT_SERVER) + "\n");
   FileWriteString(handle, StringFormat("ts=%I64d\n", (long)TimeGMT()));
   FileClose(handle);
   if(!FileMove(ACCOUNT_STAMP_TMP, 0, ACCOUNT_STAMP_FILE, FILE_REWRITE))
   {
      PrintFormat("[RX] Account stamp: could not replace %s (%d)", ACCOUNT_STAMP_FILE, GetLastError());
      return false;
   }
   return true;
}

// Rewrites the stamp at most once a minute; force writes it now.
void MaybeWriteAccountStamp(const bool force)
{
   static ulong lastMs = 0;
   ulong nowMs = GetTickCount64();
   if(!force && lastMs != 0 && nowMs - lastMs < 60000) return;
   lastMs = nowMs;
   WriteAccountStamp();
}

// The stamp is removed only when the EA is taken off, the account changes or
// the terminal closes. A recompile, chart or input change keeps it.
void DeleteAccountStampOnDeinit(const int reason)
{
   if(reason == REASON_REMOVE || reason == REASON_ACCOUNT || reason == REASON_CLOSE)
      FileDelete(ACCOUNT_STAMP_FILE);
}

#endif