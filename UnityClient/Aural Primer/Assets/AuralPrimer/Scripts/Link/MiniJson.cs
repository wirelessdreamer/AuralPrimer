// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// A small JSON reader, for payloads too nested to field-scan.
//
// The keys CHART is read by scanning for "on", "off" and "pitch" inside each
// brace pair, which works because a keys note is flat. A fretted chart is not:
// tuning and technique keys are arrays, hand positions are objects inside an
// array, and a chord name is free text that can contain anything a scan would
// mistake for a field. JsonUtility cannot help either -- it reads no bare
// arrays and cannot tell an absent field from a zero one, and "no fret" must
// not read as the open string.
//
// So this parses into plain values: objects to Dictionary<string, object>,
// arrays to List<object>, numbers to double, and strings, bools and null as
// themselves. Free of UnityEngine, so it is tested in a plain runner.

using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace AuralPrimer.Link
{
    public static class MiniJson
    {
        /// <summary>Parse a JSON document. Throws FormatException if malformed.</summary>
        public static object Parse(string json)
        {
            if (json == null) throw new FormatException("no JSON");
            var reader = new Reader(json);
            reader.SkipSpace();
            var value = reader.ReadValue();
            reader.SkipSpace();
            if (!reader.AtEnd) throw reader.Error("trailing characters");
            return value;
        }

        sealed class Reader
        {
            readonly string _s;
            int _at;

            public Reader(string s) => _s = s;

            public bool AtEnd => _at >= _s.Length;

            public FormatException Error(string what) =>
                new FormatException($"JSON: {what} at {_at}");

            public void SkipSpace()
            {
                while (_at < _s.Length && char.IsWhiteSpace(_s[_at])) _at++;
            }

            public object ReadValue()
            {
                if (AtEnd) throw Error("unexpected end");
                switch (_s[_at])
                {
                    case '{': return ReadObject();
                    case '[': return ReadArray();
                    case '"': return ReadString();
                    case 't': Expect("true"); return true;
                    case 'f': Expect("false"); return false;
                    case 'n': Expect("null"); return null;
                    default: return ReadNumber();
                }
            }

            void Expect(string word)
            {
                if (string.CompareOrdinal(_s, _at, word, 0, word.Length) != 0) throw Error($"expected {word}");
                _at += word.Length;
            }

            Dictionary<string, object> ReadObject()
            {
                var obj = new Dictionary<string, object>();
                _at++; // {
                SkipSpace();
                if (!AtEnd && _s[_at] == '}') { _at++; return obj; }
                while (true)
                {
                    SkipSpace();
                    if (AtEnd || _s[_at] != '"') throw Error("expected a key");
                    var key = ReadString();
                    SkipSpace();
                    if (AtEnd || _s[_at] != ':') throw Error("expected ':'");
                    _at++;
                    SkipSpace();
                    obj[key] = ReadValue();
                    SkipSpace();
                    if (AtEnd) throw Error("unterminated object");
                    if (_s[_at] == ',') { _at++; continue; }
                    if (_s[_at] == '}') { _at++; return obj; }
                    throw Error("expected ',' or '}'");
                }
            }

            List<object> ReadArray()
            {
                var list = new List<object>();
                _at++; // [
                SkipSpace();
                if (!AtEnd && _s[_at] == ']') { _at++; return list; }
                while (true)
                {
                    SkipSpace();
                    list.Add(ReadValue());
                    SkipSpace();
                    if (AtEnd) throw Error("unterminated array");
                    if (_s[_at] == ',') { _at++; continue; }
                    if (_s[_at] == ']') { _at++; return list; }
                    throw Error("expected ',' or ']'");
                }
            }

            string ReadString()
            {
                _at++; // opening quote
                var sb = new StringBuilder();
                while (true)
                {
                    if (AtEnd) throw Error("unterminated string");
                    var c = _s[_at++];
                    if (c == '"') return sb.ToString();
                    if (c != '\\') { sb.Append(c); continue; }
                    if (AtEnd) throw Error("unterminated escape");
                    var e = _s[_at++];
                    switch (e)
                    {
                        case '"': sb.Append('"'); break;
                        case '\\': sb.Append('\\'); break;
                        case '/': sb.Append('/'); break;
                        case 'b': sb.Append('\b'); break;
                        case 'f': sb.Append('\f'); break;
                        case 'n': sb.Append('\n'); break;
                        case 'r': sb.Append('\r'); break;
                        case 't': sb.Append('\t'); break;
                        case 'u':
                            if (_at + 4 > _s.Length) throw Error("short \\u escape");
                            sb.Append((char)int.Parse(_s.Substring(_at, 4), NumberStyles.HexNumber,
                                                      CultureInfo.InvariantCulture));
                            _at += 4;
                            break;
                        default: throw Error($"bad escape \\{e}");
                    }
                }
            }

            double ReadNumber()
            {
                var start = _at;
                while (_at < _s.Length && (char.IsDigit(_s[_at]) || _s[_at] is '-' or '+' or '.' or 'e' or 'E'))
                {
                    _at++;
                }
                if (_at == start) throw Error("unexpected character");
                if (!double.TryParse(_s.Substring(start, _at - start), NumberStyles.Float,
                                     CultureInfo.InvariantCulture, out var value))
                {
                    throw Error("bad number");
                }
                return value;
            }
        }
    }
}
