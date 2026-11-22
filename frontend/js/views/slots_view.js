export const slotsViewTemplate = `
  <div v-show="tab===0" class="panel">
    <h2>档期盘</h2>
    <div class="dash">
      <div class="cell"><div class="n">{{ dash.slots_open }}</div>开放档</div>
      <div class="cell"><div class="n">{{ dash.holds_open }}</div>占位中</div>
      <div class="cell"><div class="n">{{ dash.bookings }}</div>正式册</div>
      <div class="cell"><div class="n">{{ dash.waitlist }}</div>候补</div>
    </div>
    <div>
      <input v-model="slotForm.code" placeholder="编码" />
      <input v-model="slotForm.title" placeholder="标题" />
      <input v-model="slotForm.start_at" placeholder="开始" />
      <input v-model="slotForm.end_at" placeholder="结束" />
      <input v-model.number="slotForm.capacity" type="number" min="1" style="width:5rem" placeholder="容量" />
      <button @click="doCreateSlot">建档（同落初代）</button>
    </div>
    <p class="err" v-if="actionErr">{{ actionErr }}</p>
    <table>
      <thead><tr><th>编码</th><th>标题</th><th>容量</th><th>已订</th><th>态</th><th>代</th></tr></thead>
      <tbody>
        <tr v-for="s in slots" :key="s.id">
          <td>{{ s.code }}</td><td>{{ s.title }}</td>
          <td>{{ s.capacity }}</td><td>{{ s.booked }}</td>
          <td>{{ s.status }}</td>
          <td :class="genOf(s) ? '' : 'err'">{{ genOf(s) ? genOf(s) : '缺代' }}</td>
        </tr>
      </tbody>
    </table>
  </div>
`;
