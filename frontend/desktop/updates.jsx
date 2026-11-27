import React from "react";

export default function Updates({ state, showSettings = true }) {
  return showSettings ? <section className="settings-group"><h2>版本</h2><p>获客助手 {state.version}</p><p>请使用商用安装包覆盖升级。</p></section> : null;
}
