import { Injectable } from '@angular/core';
import { BehaviorSubject } from 'rxjs';

@Injectable({ providedIn: 'root' })
export class DateSelectionService {
  private datesSubject = new BehaviorSubject<string[]>([]); // DD.MM.YYYY values
  private indexSubject = new BehaviorSubject<number>(0);
  private dateIsoSubject = new BehaviorSubject<string>(''); // YYYY-MM-DD

  readonly dates$ = this.datesSubject.asObservable();
  readonly index$ = this.indexSubject.asObservable();
  readonly date$ = this.dateIsoSubject.asObservable();

  setAvailableDates(dates: string[]) {
    this.datesSubject.next(dates);
    if (dates.length > 0) {
      const idx = Math.max(0, dates.length - 1);
      this.setSelectedIndex(idx);
    } else {
      this.indexSubject.next(0);
      this.dateIsoSubject.next('');
    }
  }

  setSelectedIndex(i: number) {
    const dates = this.datesSubject.getValue();
    const idx = Math.max(0, Math.min(i, Math.max(0, dates.length - 1)));
    this.indexSubject.next(idx);
    const dd = dates[idx];
    if (dd) this.dateIsoSubject.next(this.toIso(dd));
  }

  setDateIso(iso: string) {
    // try to find index for the iso in known dates; otherwise just set iso
    const dates = this.datesSubject.getValue();
    const dd = this.fromIso(iso);
    const idx = dates.indexOf(dd);
    if (idx !== -1) {
      this.setSelectedIndex(idx);
    } else {
      this.dateIsoSubject.next(iso);
    }
  }

   prev() {
     const currentIso = this.dateIsoSubject.getValue();
     if (!currentIso) return;
     const prevIso = this.addDaysIso(currentIso, -1);
     this.dateIsoSubject.next(prevIso);
   }

   next() {
     const currentIso = this.dateIsoSubject.getValue();
     if (!currentIso) return;
     const nextIso = this.addDaysIso(currentIso, 1);
     this.dateIsoSubject.next(nextIso);
   }

  private addDaysIso(iso: string, delta: number): string {
    const p = iso.split('-');
    if (p.length !== 3) return iso;
    const dt = new Date(Number(p[0]), Number(p[1]) - 1, Number(p[2]));
    dt.setDate(dt.getDate() + delta);
    const y = dt.getFullYear();
    const m = String(dt.getMonth() + 1).padStart(2, '0');
    const d = String(dt.getDate()).padStart(2, '0');
    return `${y}-${m}-${d}`;
  }

  private toIso(ddmmyyyy: string): string {
    const parts = ddmmyyyy.split('.');
    if (parts.length !== 3) return '';
    return `${parts[2]}-${parts[1].padStart(2, '0')}-${parts[0].padStart(2, '0')}`;
  }

  private fromIso(iso: string): string {
    const p = iso.split('-');
    if (p.length !== 3) return '';
    return `${p[2].padStart(2, '0')}.${p[1].padStart(2, '0')}.${p[0]}`;
  }
}

